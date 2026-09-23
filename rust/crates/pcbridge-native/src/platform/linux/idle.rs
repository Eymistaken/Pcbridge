//! User idle time on KDE Plasma, from the `ext_idle_notifier_v1` protocol.
//!
//! KWin does not answer `GetSessionIdleTime` on Wayland ("not supported on
//! this platform", measured on Plasma 6.7.5); a Wayland client has to hold
//! an idle notification open instead. `pcbridge-native idle-watch` is that
//! client: the daemon starts it on Plasma, and it records every change in
//! a small state file under `$XDG_RUNTIME_DIR/pcbridge/`. The Python gate
//! and the helper's own write check both read the file.
//!
//! The notification fires after `NOTIFY_TIMEOUT_MS` without input
//! ("idled") and again on the next input ("resumed"). So while idle the
//! idle time is exact to the millisecond (`now - since`), and while active
//! it is known to be below the timeout and reported as 0, which is the
//! safe side for a check that refuses writes while someone is at the
//! machine. A record whose writer is gone is not trusted.

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use wayland_client::globals::{GlobalListContents, registry_queue_init};
use wayland_client::protocol::{wl_registry, wl_seat};
use wayland_client::{Connection, Dispatch, Proxy, QueueHandle};
use wayland_protocols::ext::idle_notify::v1::client::{
    ext_idle_notification_v1, ext_idle_notifier_v1,
};

pub const IDLE_STATE_FILE: &str = "idle.json";
pub const NOTIFY_TIMEOUT_MS: u32 = 1000;
const RECORD_VERSION: u32 = 1;
/// The word in the writer's command line that proves it is the watcher.
const WATCH_ARGUMENT: &str = "idle-watch";

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub struct IdleRecord {
    pub version: u32,
    pub pid: u32,
    pub timeout_ms: u32,
    pub idle: bool,
    pub since_unix_ms: u64,
}

impl IdleRecord {
    /// Milliseconds since the last input, as far as this record can say.
    #[must_use]
    pub fn idle_ms(&self, now_unix_ms: u64) -> u64 {
        if self.idle {
            now_unix_ms.saturating_sub(self.since_unix_ms)
        } else {
            0
        }
    }
}

#[must_use]
pub fn unix_ms(time: SystemTime) -> u64 {
    time.duration_since(UNIX_EPOCH)
        .map_or(0, |d| u64::try_from(d.as_millis()).unwrap_or(u64::MAX))
}

/// `$XDG_RUNTIME_DIR/pcbridge/idle.json`, when there is a runtime dir.
#[must_use]
pub fn state_path() -> Option<PathBuf> {
    std::env::var_os("XDG_RUNTIME_DIR")
        .filter(|dir| !dir.is_empty())
        .map(|dir| PathBuf::from(dir).join("pcbridge").join(IDLE_STATE_FILE))
}

fn writer_alive(pid: u32) -> bool {
    fs::read(format!("/proc/{pid}/cmdline")).is_ok_and(|raw| {
        raw.split(|byte| *byte == 0)
            .any(|argument| argument == WATCH_ARGUMENT.as_bytes())
    })
}

/// The idle time the watcher recorded, or None when it cannot be trusted
/// (no file, a malformed or foreign record, a writer that is gone).
#[must_use]
pub fn read_idle_ms(path: &Path, now_unix_ms: u64) -> Option<u64> {
    let record: IdleRecord = serde_json::from_slice(&fs::read(path).ok()?).ok()?;
    if record.version != RECORD_VERSION || !writer_alive(record.pid) {
        return None;
    }
    Some(record.idle_ms(now_unix_ms))
}

fn write_record(path: &Path, record: &IdleRecord) -> std::io::Result<()> {
    let temporary = path.with_extension("json.tmp");
    let mut file = fs::File::create(&temporary)?;
    file.write_all(&serde_json::to_vec(record).map_err(std::io::Error::other)?)?;
    file.sync_all()?;
    fs::rename(&temporary, path)
}

struct Watcher {
    path: PathBuf,
    failure: Option<String>,
}

impl Watcher {
    fn record(&mut self, idle: bool) {
        let now = unix_ms(SystemTime::now());
        let since = if idle {
            now.saturating_sub(u64::from(NOTIFY_TIMEOUT_MS))
        } else {
            now
        };
        let record = IdleRecord {
            version: RECORD_VERSION,
            pid: std::process::id(),
            timeout_ms: NOTIFY_TIMEOUT_MS,
            idle,
            since_unix_ms: since,
        };
        if let Err(error) = write_record(&self.path, &record) {
            self.failure = Some(format!("cannot write {}: {error}", self.path.display()));
        }
    }
}

impl Dispatch<wl_registry::WlRegistry, GlobalListContents> for Watcher {
    fn event(
        _: &mut Self,
        _: &wl_registry::WlRegistry,
        _: wl_registry::Event,
        _: &GlobalListContents,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
    }
}

impl Dispatch<wl_seat::WlSeat, ()> for Watcher {
    fn event(
        _: &mut Self,
        _: &wl_seat::WlSeat,
        _: wl_seat::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
    }
}

impl Dispatch<ext_idle_notifier_v1::ExtIdleNotifierV1, ()> for Watcher {
    fn event(
        _: &mut Self,
        _: &ext_idle_notifier_v1::ExtIdleNotifierV1,
        _: ext_idle_notifier_v1::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
    }
}

impl Dispatch<ext_idle_notification_v1::ExtIdleNotificationV1, ()> for Watcher {
    fn event(
        state: &mut Self,
        _: &ext_idle_notification_v1::ExtIdleNotificationV1,
        event: ext_idle_notification_v1::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        match event {
            ext_idle_notification_v1::Event::Idled => state.record(true),
            ext_idle_notification_v1::Event::Resumed => state.record(false),
            _ => {}
        }
    }
}

/// Hold an idle notification open and record every change until the
/// compositor goes away. Returns why it stopped.
pub fn watch(path: &Path) -> String {
    if let Some(parent) = path.parent()
        && let Err(error) = fs::create_dir_all(parent)
    {
        return format!("cannot create {}: {error}", parent.display());
    }
    let connection = match Connection::connect_to_env() {
        Ok(connection) => connection,
        Err(error) => return format!("no Wayland connection: {error}"),
    };
    let (globals, mut queue) = match registry_queue_init::<Watcher>(&connection) {
        Ok(pair) => pair,
        Err(error) => return format!("Wayland registry failed: {error}"),
    };
    let handle = queue.handle();
    let seat: wl_seat::WlSeat = match globals.bind(&handle, 1..=1, ()) {
        Ok(seat) => seat,
        Err(error) => return format!("no wl_seat: {error}"),
    };
    let notifier: ext_idle_notifier_v1::ExtIdleNotifierV1 = match globals.bind(&handle, 1..=2, ()) {
        Ok(notifier) => notifier,
        Err(error) => return format!("the compositor offers no ext_idle_notifier_v1: {error}"),
    };
    // Version 2 can ignore idle inhibitors (a playing video): pcbridge asks
    // about input, not about whether the screen may blank.
    let _notification = if notifier.version() >= 2 {
        notifier.get_input_idle_notification(NOTIFY_TIMEOUT_MS, &seat, &handle, ())
    } else {
        notifier.get_idle_notification(NOTIFY_TIMEOUT_MS, &seat, &handle, ())
    };
    let mut watcher = Watcher {
        path: path.to_path_buf(),
        failure: None,
    };
    // Until the first event, assume someone is at the machine.
    watcher.record(false);
    loop {
        if let Some(failure) = watcher.failure.take() {
            return failure;
        }
        if let Err(error) = queue.blocking_dispatch(&mut watcher) {
            return format!("Wayland connection ended: {error}");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{IdleRecord, read_idle_ms, write_record};

    fn record(pid: u32, idle: bool, since: u64) -> IdleRecord {
        IdleRecord {
            version: 1,
            pid,
            timeout_ms: 1000,
            idle,
            since_unix_ms: since,
        }
    }

    #[test]
    fn idle_counts_from_the_last_input_and_active_is_zero() {
        assert_eq!(record(1, true, 10_000).idle_ms(75_000), 65_000);
        assert_eq!(record(1, false, 10_000).idle_ms(75_000), 0);
        assert_eq!(record(1, true, 80_000).idle_ms(75_000), 0);
    }

    #[test]
    fn a_record_without_its_watcher_is_not_trusted() {
        let dir = std::env::temp_dir().join(format!("pcbridge-idle-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("idle.json");
        // This test process is alive but is not an idle watcher.
        write_record(&path, &record(std::process::id(), true, 0)).unwrap();
        assert_eq!(read_idle_ms(&path, 5_000), None);
        std::fs::write(&path, b"not json").unwrap();
        assert_eq!(read_idle_ms(&path, 5_000), None);
        assert_eq!(read_idle_ms(&dir.join("missing.json"), 5_000), None);
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
