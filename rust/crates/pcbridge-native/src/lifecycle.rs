//! Grant-bound native resource lifecycle with a fail-closed watchdog.

use std::fmt;
use std::io;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use pcbridge_core::{DesktopLease, LEASE_STATE_FILE, LeaseToken};

use crate::platform::linux::desktop_state::{
    ActivityState, DesktopStateProvider, ScreenLockState, SessionDesktopState, UnknownDesktopState,
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

/// A native resource that must be released the moment the grant stops holding.
///
/// The watchdogs used to express this as a single `AtomicBool`, which is enough
/// while the only "resource" is a test flag. A screencast session is not: it is
/// visible to the user as the sharing indicator, and clearing a boolean does not
/// turn that indicator off. So a resource registers itself here and is told,
/// from the watchdog thread, to let go.
///
/// Implementations must return promptly and must tolerate being called twice.
/// They are invoked while nothing else is locked, but on a 100 ms watchdog:
/// blocking here delays every later revoke.
pub trait FailClosed: Send + Sync {
    fn close_fail_closed(&self, reason: LifecycleFailure);
}

#[derive(Default)]
struct FailClosedRegistry {
    resources: Mutex<Vec<Arc<dyn FailClosed>>>,
}

impl FailClosedRegistry {
    fn register(&self, resource: Arc<dyn FailClosed>) {
        if let Ok(mut resources) = self.resources.lock() {
            resources.push(resource);
        }
    }

    /// Snapshot first, then call: holding the registry lock across a resource's
    /// own locking is how a fail-closed path turns into a deadlock.
    fn close_all(&self, reason: LifecycleFailure) {
        let snapshot = match self.resources.lock() {
            Ok(resources) => resources.clone(),
            Err(poisoned) => poisoned.into_inner().clone(),
        };
        for resource in snapshot {
            resource.close_fail_closed(reason);
        }
    }
}

pub struct Lifecycle {
    state_path: PathBuf,
    expected: Option<LeaseToken>,
    revoked: Arc<AtomicBool>,
    resource_open: Arc<AtomicBool>,
    desktop_state: Arc<dyn DesktopStateProvider>,
    resources: Arc<FailClosedRegistry>,
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
        let desktop_state: Arc<dyn DesktopStateProvider> = SessionDesktopState::connect()
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
        let resources = Arc::new(FailClosedRegistry::default());
        let stop = Arc::new(AtomicBool::new(false));

        let lease_path = state_path.clone();
        let lease_token = expected.clone();
        let lease_revoked = Arc::clone(&revoked);
        let lease_resource = Arc::clone(&resource_open);
        let lease_stop = Arc::clone(&stop);
        let lease_resources = Arc::clone(&resources);
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
                        lease_resource.store(false, Ordering::Release);
                        // Only on the edge. `revoked` starts true when there is
                        // no grant at all, so a helper that was never granted
                        // anything does not wake registered resources every
                        // 100 ms for the rest of its life.
                        if !lease_revoked.swap(true, Ordering::AcqRel) {
                            lease_resources.close_all(LifecycleFailure::Revoked);
                        }
                    }
                }
            })?;

        let desktop_resource = Arc::clone(&resource_open);
        let desktop_stop = Arc::clone(&stop);
        let desktop_resources = Arc::clone(&resources);
        let watch_desktop_state = Arc::clone(&desktop_state);
        let desktop_watchdog = match thread::Builder::new()
            .name(format!("pcbridge-native-lock-{}", std::process::id()))
            .spawn(move || {
                // A screen lock is not a revoke: it ends when the user comes
                // back. So the edge is tracked locally and nothing latches --
                // resources are told to close, and a later request may open
                // again once the guard says the screen is unlocked.
                let mut was_unlocked = true;
                while !desktop_stop.load(Ordering::Acquire) {
                    let lock = watch_desktop_state.wait_for_lock_change(WATCHDOG_INTERVAL);
                    if desktop_stop.load(Ordering::Acquire) {
                        break;
                    }
                    let unlocked = lock.state == ScreenLockState::KnownUnlocked;
                    if !unlocked {
                        desktop_resource.store(false, Ordering::Release);
                        if was_unlocked {
                            desktop_resources.close_all(match lock.state {
                                ScreenLockState::KnownLocked => LifecycleFailure::ScreenLocked,
                                _ => LifecycleFailure::LockStateUnknown,
                            });
                        }
                    }
                    was_unlocked = unlocked;
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
            resources,
            stop,
            lease_watchdog: Some(lease_watchdog),
            desktop_watchdog: Some(desktop_watchdog),
        })
    }

    /// Bind a resource to this grant. It is closed, from the watchdog thread,
    /// as soon as the grant is revoked or the screen stops being known-unlocked.
    pub fn register_fail_closed(&self, resource: Arc<dyn FailClosed>) {
        self.resources.register(resource);
    }

    #[must_use]
    pub fn lease_bound(&self) -> bool {
        self.expected.is_some()
    }

    /// Whether request metadata names the exact grant snapshot this process
    /// bound during `initialize`.
    #[must_use]
    pub fn matches_token(&self, grant_id: &str, revoke_epoch: u64) -> bool {
        self.expected.as_ref().is_some_and(|token| {
            token.grant_id() == grant_id && token.revoke_epoch() == revoke_epoch
        })
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
        self.resource_open.store(false, Ordering::Release);
        // The request path can notice a revoke before the 100 ms watchdog does.
        // Whichever sees it first owns the edge, so registered resources are
        // closed exactly once either way.
        if !self.revoked.swap(true, Ordering::AcqRel) {
            self.resources.close_all(LifecycleFailure::Revoked);
        }
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
