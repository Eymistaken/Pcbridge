//! Grant-bound native resource lifecycle with a fail-closed watchdog.

use std::fmt;
use std::io;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread::{self, JoinHandle};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use pcbridge_core::{DesktopLease, LEASE_STATE_FILE, LeaseToken};

use crate::platform::linux::desktop_state::{
    ActivityState, DesktopStateProvider, GnomeDesktopState, ScreenLockState, UnknownDesktopState,
};

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
pub enum LifecycleFailure {
    GrantRequired,
    Revoked,
    ScreenLocked,
    LockStateUnknown,
    UserActive,
    ActivityUnknown,
}

impl LifecycleFailure {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::GrantRequired => "GRANT_REQUIRED",
            Self::Revoked => "REVOKED",
            Self::ScreenLocked => "SCREEN_LOCKED",
            Self::LockStateUnknown => "LOCK_STATE_UNKNOWN",
            Self::UserActive => "USER_ACTIVE",
            Self::ActivityUnknown => "ACTIVITY_UNKNOWN",
        }
    }
}

pub type LeaseFailure = LifecycleFailure;

pub struct Lifecycle {
    state_path: PathBuf,
    expected: Option<LeaseToken>,
    revoked: Arc<AtomicBool>,
    resource_open: Arc<AtomicBool>,
    desktop_state: Arc<dyn DesktopStateProvider>,
    stop: Arc<AtomicBool>,
    lease_watchdog: Option<JoinHandle<()>>,
    desktop_watchdog: Option<JoinHandle<()>>,
}

impl fmt::Debug for Lifecycle {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("Lifecycle")
            .field("state_path", &self.state_path)
            .field("expected", &self.expected)
            .field("revoked", &self.revoked.load(Ordering::Acquire))
            .field("resource_open", &self.resource_open.load(Ordering::Acquire))
            .finish_non_exhaustive()
    }
}

impl Lifecycle {
    pub fn start(state_dir: &Path) -> Result<Self, io::Error> {
        let desktop_state: Arc<dyn DesktopStateProvider> = GnomeDesktopState::connect()
            .map_or_else(
                |_| Arc::new(UnknownDesktopState) as Arc<dyn DesktopStateProvider>,
                |provider| Arc::new(provider) as Arc<dyn DesktopStateProvider>,
            );
        Self::start_with_provider(state_dir, desktop_state)
    }

    pub fn start_with_provider(
        state_dir: &Path,
        desktop_state: Arc<dyn DesktopStateProvider>,
    ) -> Result<Self, io::Error> {
        let state_path = state_dir.join(LEASE_STATE_FILE);
        let expected = DesktopLease::read(&state_path)
            .ok()
            .and_then(|lease| lease.native_token_at(unix_time()));
        let revoked = Arc::new(AtomicBool::new(expected.is_none()));
        let resource_open = Arc::new(AtomicBool::new(false));
        let stop = Arc::new(AtomicBool::new(false));

        let lease_path = state_path.clone();
        let lease_token = expected.clone();
        let lease_revoked = Arc::clone(&revoked);
        let lease_resource = Arc::clone(&resource_open);
        let lease_stop = Arc::clone(&stop);
        let lease_watchdog = thread::Builder::new()
            .name(format!("pcbridge-native-lease-{}", std::process::id()))
            .spawn(move || {
                while !lease_stop.load(Ordering::Acquire) {
                    thread::sleep(WATCHDOG_INTERVAL);
                    if lease_stop.load(Ordering::Acquire) {
                        break;
                    }
                    let valid = lease_token
                        .as_ref()
                        .is_some_and(|token| token_is_valid(&lease_path, token));
                    if !valid {
                        lease_revoked.store(true, Ordering::Release);
                        lease_resource.store(false, Ordering::Release);
                    }
                }
            })?;

        let desktop_resource = Arc::clone(&resource_open);
        let desktop_stop = Arc::clone(&stop);
        let watch_desktop_state = Arc::clone(&desktop_state);
        let desktop_watchdog = match thread::Builder::new()
            .name(format!("pcbridge-native-lock-{}", std::process::id()))
            .spawn(move || {
                while !desktop_stop.load(Ordering::Acquire) {
                    let lock = watch_desktop_state.wait_for_lock_change(WATCHDOG_INTERVAL);
                    if desktop_stop.load(Ordering::Acquire) {
                        break;
                    }
                    if lock.state != ScreenLockState::KnownUnlocked {
                        desktop_resource.store(false, Ordering::Release);
                    }
                }
            }) {
            Ok(watchdog) => watchdog,
            Err(error) => {
                stop.store(true, Ordering::Release);
                let _ = lease_watchdog.join();
                return Err(error);
            }
        };

        Ok(Self {
            state_path,
            expected,
            revoked,
            resource_open,
            desktop_state,
            stop,
            lease_watchdog: Some(lease_watchdog),
            desktop_watchdog: Some(desktop_watchdog),
        })
    }

    #[must_use]
    pub fn lease_bound(&self) -> bool {
        self.expected.is_some()
    }

    fn failure(&self) -> LifecycleFailure {
        if self.expected.is_some() {
            LifecycleFailure::Revoked
        } else {
            LifecycleFailure::GrantRequired
        }
    }

    fn validate_lease_now(&self) -> Result<(), LifecycleFailure> {
        if self.revoked.load(Ordering::Acquire) {
            return Err(self.failure());
        }
        let Some(expected) = self.expected.as_ref() else {
            return Err(LifecycleFailure::GrantRequired);
        };
        if token_is_valid(&self.state_path, expected) {
            return Ok(());
        }
        self.revoked.store(true, Ordering::Release);
        self.resource_open.store(false, Ordering::Release);
        Err(LifecycleFailure::Revoked)
    }

    pub fn validate_now(&self) -> Result<(), LifecycleFailure> {
        self.validate_lease_now()?;
        match self.desktop_state.screen_lock().state {
            ScreenLockState::KnownUnlocked => Ok(()),
            ScreenLockState::KnownLocked => {
                self.resource_open.store(false, Ordering::Release);
                Err(LifecycleFailure::ScreenLocked)
            }
            ScreenLockState::Unknown => {
                self.resource_open.store(false, Ordering::Release);
                Err(LifecycleFailure::LockStateUnknown)
            }
        }
    }

    pub fn validate_write_now(
        &self,
        force: bool,
        idle_guard_ms: u64,
    ) -> Result<(), LifecycleFailure> {
        self.validate_now()?;
        if force {
            return Ok(());
        }
        let activity = self.desktop_state.user_activity();
        match (activity.state, activity.idle_ms) {
            (ActivityState::Known, Some(idle_ms)) if idle_ms >= idle_guard_ms => Ok(()),
            (ActivityState::Known, Some(_)) => Err(LifecycleFailure::UserActive),
            _ => Err(LifecycleFailure::ActivityUnknown),
        }
    }

    #[cfg(feature = "test-harness")]
    pub fn open_test_resource(&self) -> Result<(), LifecycleFailure> {
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
        if let Some(watchdog) = self.lease_watchdog.take() {
            let _ = watchdog.join();
        }
        if let Some(watchdog) = self.desktop_watchdog.take() {
            let _ = watchdog.join();
        }
    }
}
