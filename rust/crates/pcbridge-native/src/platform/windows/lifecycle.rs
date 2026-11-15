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
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, Weak};
use std::thread::{self, JoinHandle};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use pcbridge_core::{DesktopLease, LEASE_STATE_FILE, LeaseToken};

use super::desktop_state::{self, LockState};
use crate::fail_closed::{FailClosed, LifecycleFailure};

/// How often the watchdog reads the grant and the lock, as on Linux.
const WATCHDOG_INTERVAL: Duration = Duration::from_millis(100);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Failure {
    GrantRequired,
    Revoked,
    ScreenLocked,
    LockStateUnknown,
}

impl Failure {
    #[must_use]
    pub const fn lifecycle(self) -> LifecycleFailure {
        match self {
            Self::GrantRequired => LifecycleFailure::GrantRequired,
            Self::Revoked => LifecycleFailure::Revoked,
            Self::ScreenLocked => LifecycleFailure::ScreenLocked,
            Self::LockStateUnknown => LifecycleFailure::LockStateUnknown,
        }
    }

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
    watchdog: Option<Watchdog>,
}

impl Binding {
    #[must_use]
    pub fn bind(state_dir: &Path) -> Self {
        let state_path = state_dir.join(LEASE_STATE_FILE);
        let token = DesktopLease::read(&state_path)
            .ok()
            .and_then(|lease| lease.native_token_at(unix_time()));
        let watchdog = token
            .as_ref()
            .map(|token| Watchdog::start(state_path.clone(), token.clone()));
        Self {
            state_path,
            token,
            watchdog,
        }
    }

    /// Close `resource` the moment the grant stops holding (a revoke, an
    /// expiry, a locked or unreadable session). Without a bound grant
    /// nothing can be opened, so there is nothing to watch.
    pub fn register_fail_closed(&self, resource: Weak<dyn FailClosed>) {
        if let Some(watchdog) = self.watchdog.as_ref() {
            watchdog.register(resource);
        }
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
        holds(&self.state_path, token)
    }
}

/// Whether the bound grant still holds and the session is unlocked.
fn holds(state_path: &Path, token: &LeaseToken) -> Result<(), Failure> {
    let valid =
        DesktopLease::read(state_path).is_ok_and(|lease| lease.validates_at(token, unix_time()));
    if !valid {
        return Err(Failure::Revoked);
    }
    match desktop_state::lock_state() {
        LockState::KnownUnlocked => Ok(()),
        LockState::KnownLocked => Err(Failure::ScreenLocked),
        LockState::Unknown => Err(Failure::LockStateUnknown),
    }
}

type Resources = Arc<Mutex<Vec<Weak<dyn FailClosed>>>>;

/// Every 100 ms, on its own thread: if the grant stopped holding, tell
/// every open resource to close (held keys and buttons are released by the
/// close). Edge-triggered: it fires once when the state turns bad, and
/// again only after it was good in between (a lock, an unlock, a lock).
struct Watchdog {
    resources: Resources,
    stop: Arc<AtomicBool>,
    worker: Option<JoinHandle<()>>,
}

impl Watchdog {
    fn start(state_path: PathBuf, token: LeaseToken) -> Self {
        let resources: Resources = Arc::default();
        let stop = Arc::new(AtomicBool::new(false));
        let worker = {
            let resources = Arc::clone(&resources);
            let stop = Arc::clone(&stop);
            thread::Builder::new()
                .name("pcbridge-native-watchdog".to_owned())
                .spawn(move || {
                    let mut was_good = true;
                    while !stop.load(Ordering::Acquire) {
                        match holds(&state_path, &token) {
                            Ok(()) => was_good = true,
                            Err(failure) if was_good => {
                                was_good = false;
                                close_all(&resources, failure.lifecycle());
                            }
                            Err(_) => {}
                        }
                        thread::sleep(WATCHDOG_INTERVAL);
                    }
                })
                .ok()
        };
        Self {
            resources,
            stop,
            worker,
        }
    }

    fn register(&self, resource: Weak<dyn FailClosed>) {
        let mut resources = self
            .resources
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        resources.retain(|known| known.strong_count() > 0);
        resources.push(resource);
    }
}

/// Snapshot first, then call: a resource's close takes its own lock.
fn close_all(resources: &Resources, failure: LifecycleFailure) {
    let snapshot: Vec<_> = resources
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .iter()
        .filter_map(Weak::upgrade)
        .collect();
    for resource in snapshot {
        resource.close_fail_closed(failure);
    }
}

impl Drop for Watchdog {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}
