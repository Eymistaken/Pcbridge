//! The helper's binding to one desktop grant, and the checks every protected
//! request passes (docs/native/protocol-v1.md, "Grant, revoke" and "Desktop
//! state"), on Windows.
//!
//! The helper binds once, at `initialize`, to the active grant in
//! `desktop_unlock.json` (pcbridge-core's lease). Every protected request
//! names that grant's id and epoch and the file is read again: a different
//! grant, a raised epoch or an expired one is `REVOKED`, no grant at all
//! `GRANT_REQUIRED`. A locked session is `SCREEN_LOCKED`, an unreadable lock
//! state `LOCK_STATE_UNKNOWN`. The same codes as Linux.

use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use pcbridge_core::{DesktopLease, LEASE_STATE_FILE, LeaseToken};

use super::desktop_state::{self, LockState};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Failure {
    GrantRequired,
    Revoked,
    ScreenLocked,
    LockStateUnknown,
}

impl Failure {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::GrantRequired => "GRANT_REQUIRED",
            Self::Revoked => "REVOKED",
            Self::ScreenLocked => "SCREEN_LOCKED",
            Self::LockStateUnknown => "LOCK_STATE_UNKNOWN",
        }
    }

    #[must_use]
    pub const fn message(self) -> &'static str {
        match self {
            Self::GrantRequired => "no desktop grant is open; desktop_unlock opens one",
            Self::Revoked => "the request does not match the bound desktop grant, or it ended",
            Self::ScreenLocked => "the screen is locked",
            Self::LockStateUnknown => "whether the screen is locked cannot be read",
        }
    }
}

fn unix_time() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(f64::INFINITY, |duration| duration.as_secs_f64())
}

pub struct Binding {
    state_path: PathBuf,
    token: Option<LeaseToken>,
}

impl Binding {
    #[must_use]
    pub fn bind(state_dir: &Path) -> Self {
        let state_path = state_dir.join(LEASE_STATE_FILE);
        let token = DesktopLease::read(&state_path)
            .ok()
            .and_then(|lease| lease.native_token_at(unix_time()));
        Self { state_path, token }
    }

    #[must_use]
    pub const fn is_bound(&self) -> bool {
        self.token.is_some()
    }

    /// The grant and the screen lock, in that order, for one request.
    pub fn validate(&self, grant_id: &str, revoke_epoch: u64) -> Result<(), Failure> {
        let token = self.token.as_ref().ok_or(Failure::GrantRequired)?;
        if token.grant_id() != grant_id || token.revoke_epoch() != revoke_epoch {
            return Err(Failure::Revoked);
        }
        let valid = DesktopLease::read(&self.state_path)
            .is_ok_and(|lease| lease.validates_at(token, unix_time()));
        if !valid {
            return Err(Failure::Revoked);
        }
        match desktop_state::lock_state() {
            LockState::KnownUnlocked => Ok(()),
            LockState::KnownLocked => Err(Failure::ScreenLocked),
            LockState::Unknown => Err(Failure::LockStateUnknown),
        }
    }
}
