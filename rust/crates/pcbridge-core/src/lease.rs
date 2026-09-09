use std::fs::File;
use std::io::{self, Read};
use std::path::Path;

use serde::Deserialize;
use thiserror::Error;

pub const LEASE_SCHEMA_VERSION: u32 = 1;
pub const LEASE_STATE_FILE: &str = "desktop_unlock.json";
const MAX_LEASE_BYTES: u64 = 64 * 1024;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LeaseToken {
    grant_id: String,
    revoke_epoch: u64,
}

impl LeaseToken {
    #[must_use]
    pub fn new(grant_id: impl Into<String>, revoke_epoch: u64) -> Self {
        Self {
            grant_id: grant_id.into(),
            revoke_epoch,
        }
    }

    #[must_use]
    pub fn grant_id(&self) -> &str {
        &self.grant_id
    }

    #[must_use]
    pub const fn revoke_epoch(&self) -> u64 {
        self.revoke_epoch
    }
}

#[derive(Clone, Debug, Deserialize)]
pub struct DesktopLease {
    #[serde(default)]
    schema_version: Option<u32>,
    #[serde(default)]
    grant_id: Option<String>,
    #[serde(default)]
    revoke_epoch: u64,
    #[serde(default)]
    until: f64,
    #[serde(default)]
    hard_until: f64,
}

impl DesktopLease {
    pub fn read(path: &Path) -> Result<Self, LeaseReadError> {
        let metadata = path.symlink_metadata()?;
        if !metadata.file_type().is_file() || metadata.len() > MAX_LEASE_BYTES {
            return Err(LeaseReadError::InvalidFile);
        }
        let file = File::open(path)?;
        let mut bytes = Vec::with_capacity(metadata.len() as usize);
        file.take(MAX_LEASE_BYTES + 1).read_to_end(&mut bytes)?;
        if bytes.len() as u64 > MAX_LEASE_BYTES {
            return Err(LeaseReadError::InvalidFile);
        }
        serde_json::from_slice(&bytes).map_err(LeaseReadError::InvalidJson)
    }

    #[must_use]
    pub fn is_active_at(&self, now: f64) -> bool {
        self.until.is_finite() && self.until > now
    }

    #[must_use]
    pub fn native_token_at(&self, now: f64) -> Option<LeaseToken> {
        if self.schema_version != Some(LEASE_SCHEMA_VERSION)
            || !self.is_active_at(now)
            || !self.hard_until.is_finite()
            || self.hard_until <= now
        {
            return None;
        }
        let grant_id = self.grant_id.as_deref()?;
        if grant_id.is_empty() {
            return None;
        }
        Some(LeaseToken::new(grant_id, self.revoke_epoch))
    }

    #[must_use]
    pub fn validates_at(&self, token: &LeaseToken, now: f64) -> bool {
        self.native_token_at(now).as_ref() == Some(token)
    }
}

#[derive(Debug, Error)]
pub enum LeaseReadError {
    #[error("desktop lease file is unavailable")]
    Io(#[from] io::Error),
    #[error("desktop lease file is invalid")]
    InvalidFile,
    #[error("desktop lease JSON is invalid")]
    InvalidJson(serde_json::Error),
}
