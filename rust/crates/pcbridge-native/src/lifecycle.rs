//! Grant-bound native resource lifecycle with a fail-closed watchdog.

use std::io;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread::{self, JoinHandle};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use pcbridge_core::{DesktopLease, LEASE_STATE_FILE, LeaseToken};

const WATCHDOG_INTERVAL: Duration = Duration::from_millis(100);

fn unix_time() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(f64::INFINITY, |duration| duration.as_secs_f64())
}

fn token_is_valid(path: &Path, token: &LeaseToken) -> bool {
    DesktopLease::read(path).is_ok_and(|lease| lease.validates_at(token, unix_time()))
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LeaseFailure {
    GrantRequired,
    Revoked,
}

impl LeaseFailure {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::GrantRequired => "GRANT_REQUIRED",
            Self::Revoked => "REVOKED",
        }
    }
}

#[derive(Debug)]
pub struct Lifecycle {
    state_path: PathBuf,
    expected: Option<LeaseToken>,
    revoked: Arc<AtomicBool>,
    resource_open: Arc<AtomicBool>,
    stop: Arc<AtomicBool>,
    watchdog: Option<JoinHandle<()>>,
}

impl Lifecycle {
    pub fn start(state_dir: &Path) -> Result<Self, io::Error> {
        let state_path = state_dir.join(LEASE_STATE_FILE);
        let expected = DesktopLease::read(&state_path)
            .ok()
            .and_then(|lease| lease.native_token_at(unix_time()));
        let revoked = Arc::new(AtomicBool::new(expected.is_none()));
        let resource_open = Arc::new(AtomicBool::new(false));
        let stop = Arc::new(AtomicBool::new(false));

        let watch_path = state_path.clone();
        let watch_token = expected.clone();
        let watch_revoked = Arc::clone(&revoked);
        let watch_resource = Arc::clone(&resource_open);
        let watch_stop = Arc::clone(&stop);
        let watchdog = thread::Builder::new()
            .name(format!("pcbridge-native-lease-{}", std::process::id()))
            .spawn(move || {
                while !watch_stop.load(Ordering::Acquire) {
                    thread::sleep(WATCHDOG_INTERVAL);
                    if watch_stop.load(Ordering::Acquire) {
                        break;
                    }
                    let valid = watch_token
                        .as_ref()
                        .is_some_and(|token| token_is_valid(&watch_path, token));
                    if !valid {
                        watch_revoked.store(true, Ordering::Release);
                        watch_resource.store(false, Ordering::Release);
                    }
                }
            })?;

        Ok(Self {
            state_path,
            expected,
            revoked,
            resource_open,
            stop,
            watchdog: Some(watchdog),
        })
    }

    #[must_use]
    pub fn lease_bound(&self) -> bool {
        self.expected.is_some()
    }

    fn failure(&self) -> LeaseFailure {
        if self.expected.is_some() {
            LeaseFailure::Revoked
        } else {
            LeaseFailure::GrantRequired
        }
    }

    pub fn validate_now(&self) -> Result<(), LeaseFailure> {
        if self.revoked.load(Ordering::Acquire) {
            return Err(self.failure());
        }
        let Some(expected) = self.expected.as_ref() else {
            return Err(LeaseFailure::GrantRequired);
        };
        if token_is_valid(&self.state_path, expected) {
            return Ok(());
        }
        self.revoked.store(true, Ordering::Release);
        self.resource_open.store(false, Ordering::Release);
        Err(LeaseFailure::Revoked)
    }

    #[cfg(feature = "test-harness")]
    pub fn open_test_resource(&self) -> Result<(), LeaseFailure> {
        self.validate_now()?;
        self.resource_open.store(true, Ordering::Release);
        Ok(())
    }

    #[cfg(feature = "test-harness")]
    #[must_use]
    pub fn test_resource_is_open(&self) -> bool {
        self.resource_open.load(Ordering::Acquire)
    }
}

impl Drop for Lifecycle {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        self.resource_open.store(false, Ordering::Release);
        if let Some(watchdog) = self.watchdog.take() {
            let _ = watchdog.join();
        }
    }
}
