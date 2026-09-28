//! Fresh, process- and grant-bound evidence from the native frame owner.

use std::fs::{File, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::path::Path;
use std::time::SystemTime;

use pcbridge_core::{DesktopLease, LEASE_LOCK_FILE, LEASE_STATE_FILE, LeaseToken};
use serde::{Deserialize, Serialize};

use super::idle::{unix_ms, writer_start_ticks};

pub const STATE_FILE: &str = "glow.json";
pub const MAX_AGE_MS: u64 = 1000;
const MAX_RECORD_BYTES: u64 = 8192;

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct FrameRecord {
    pub version: u32,
    pub ready: bool,
    pub pid: u32,
    pub writer_start_ticks: u64,
    pub owner_pid: u32,
    pub owner_start_ticks: u64,
    pub grant_id: String,
    pub revoke_epoch: u64,
    pub wayland_display: String,
    pub hyprland_instance: String,
    pub topology_id: String,
    pub strip_count: u32,
    pub presented_unix_ms: u64,
}

impl FrameRecord {
    #[must_use]
    pub fn matches(&self, token: &LeaseToken, display: &str, signature: &str, now: u64) -> bool {
        self.version == 1
            && self.ready
            && self.pid > 0
            && self.writer_start_ticks > 0
            && self.owner_pid > 0
            && self.owner_start_ticks > 0
            && self.grant_id == token.grant_id()
            && self.revoke_epoch == token.revoke_epoch()
            && !display.is_empty()
            && self.wayland_display == display
            && !signature.is_empty()
            && self.hyprland_instance == signature
            && self.topology_id.starts_with("v1|")
            && self.topology_id.len() <= 2048
            && self.strip_count >= 4
            && self.strip_count <= 64
            && self.strip_count % 4 == 0
            && self.presented_unix_ms <= now
            && now - self.presented_unix_ms <= MAX_AGE_MS
    }

    pub fn write(&self, directory: &Path) -> std::io::Result<()> {
        // Serialize with Python LeaseStore's flock, then check identity under
        // that lock. Replacement/revoke can never be clobbered by an old
        // observer's delayed final write.
        let path = directory.join(LEASE_LOCK_FILE);
        let lock = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .mode(0o600)
            .open(&path)?;
        let info = path.symlink_metadata()?;
        let opened = lock.metadata()?;
        let uid = std::fs::metadata("/proc/self")?.uid();
        if !info.is_file()
            || info.uid() != uid
            || info.mode() & 0o077 != 0
            || opened.dev() != info.dev()
            || opened.ino() != info.ino()
        {
            return Err(std::io::Error::other("Invalid lease lock file"));
        }
        lock.lock()?;
        let expected = LeaseToken::new(&self.grant_id, self.revoke_epoch);
        if !DesktopLease::read(&directory.join(LEASE_STATE_FILE))
            .is_ok_and(|lease| lease.has_identity(&expected))
        {
            return Ok(());
        }
        let temporary = directory.join(format!(
            ".glow-{}-{}.tmp",
            self.pid, self.writer_start_ticks
        ));
        let mut created = false;
        let result = (|| {
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(&temporary)?;
            created = true;
            file.write_all(&serde_json::to_vec(self).map_err(std::io::Error::other)?)?;
            file.sync_all()?;
            std::fs::rename(&temporary, directory.join(STATE_FILE))
        })();
        if result.is_err() && created {
            let _ = std::fs::remove_file(temporary);
        }
        result
    }
}

fn writer_matches_binary(
    pid: u32,
    directory: &Path,
    token: &LeaseToken,
    display: &str,
    signature: &str,
) -> bool {
    let Ok(raw) = std::fs::read(format!("/proc/{pid}/cmdline")) else {
        return false;
    };
    let arguments: Vec<_> = raw.split(|byte| *byte == 0).collect();
    let epoch = token.revoke_epoch().to_string();
    if arguments.get(1).copied() != Some(b"glow-watch".as_slice())
        || arguments.get(2).copied() != Some(directory.as_os_str().as_encoded_bytes())
        || arguments.get(3).copied() != Some(token.grant_id().as_bytes())
        || arguments.get(4).copied() != Some(epoch.as_bytes())
    {
        return false;
    }
    let environment = || -> Option<bool> {
        let file = File::open(format!("/proc/{pid}/environ")).ok()?;
        let mut raw = Vec::new();
        file.take(65537).read_to_end(&mut raw).ok()?;
        if raw.len() > 65536 {
            return None;
        }
        let entries: Vec<_> = raw.split(|byte| *byte == 0).collect();
        Some(
            entries.contains(&format!("WAYLAND_DISPLAY={display}").as_bytes())
                && entries.contains(&format!("HYPRLAND_INSTANCE_SIGNATURE={signature}").as_bytes()),
        )
    };
    if environment() != Some(true) {
        return false;
    }
    let same = || -> Option<bool> {
        let actual = std::fs::metadata(format!("/proc/{pid}/exe")).ok()?;
        let expected = std::env::current_exe().ok()?.metadata().ok()?;
        Some(
            actual.dev() == expected.dev()
                && actual.ino() == expected.ino()
                && actual.uid() == expected.uid(),
        )
    };
    same() == Some(true)
}

pub(crate) fn parent_pid(pid: u32) -> Option<u32> {
    std::fs::read_to_string(format!("/proc/{pid}/stat"))
        .ok()?
        .rsplit_once(')')?
        .1
        .split_whitespace()
        .nth(1)?
        .parse()
        .ok()
}

/// Metadata is also checked on the opened file. An absent, foreign, stale,
/// dead, or replaced writer must never enable invisible desktop control.
#[must_use]
pub fn read(directory: &Path, token: &LeaseToken) -> Option<FrameRecord> {
    let path = directory.join(STATE_FILE);
    let uid = std::fs::metadata("/proc/self").ok()?.uid();
    let info = path.symlink_metadata().ok()?;
    if !info.is_file()
        || info.uid() != uid
        || info.mode() & 0o077 != 0
        || info.len() > MAX_RECORD_BYTES
    {
        return None;
    }
    let file = File::open(path).ok()?;
    let opened = file.metadata().ok()?;
    if opened.dev() != info.dev() || opened.ino() != info.ino() {
        return None;
    }
    let mut raw = Vec::new();
    file.take(MAX_RECORD_BYTES + 1).read_to_end(&mut raw).ok()?;
    if raw.len() as u64 > MAX_RECORD_BYTES {
        return None;
    }
    let record: FrameRecord = serde_json::from_slice(&raw).ok()?;
    let display = std::env::var("WAYLAND_DISPLAY").ok()?;
    let signature = std::env::var("HYPRLAND_INSTANCE_SIGNATURE").ok()?;
    if !record.matches(token, &display, &signature, unix_ms(SystemTime::now()))
        || writer_start_ticks(record.pid) != Some(record.writer_start_ticks)
        || writer_start_ticks(record.owner_pid) != Some(record.owner_start_ticks)
        || parent_pid(record.pid) != Some(record.owner_pid)
        || std::fs::metadata(format!("/proc/{}", record.pid))
            .ok()?
            .uid()
            != uid
        || std::fs::metadata(format!("/proc/{}", record.owner_pid))
            .ok()?
            .uid()
            != uid
        || !writer_matches_binary(record.pid, directory, token, &display, &signature)
    {
        return None;
    }
    Some(record)
}
