#![cfg(feature = "test-harness")]

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicU8, AtomicU64, Ordering};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use pcbridge_native::lifecycle::{Lifecycle, LifecycleFailure};
use pcbridge_native::platform::linux::desktop_state::{
    ActivityObservation, ActivityState, DesktopStateProvider, ScreenLockObservation,
    ScreenLockState,
};
use serde_json::json;

fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock should follow the Unix epoch")
        .as_secs_f64()
}

fn fixture_root() -> PathBuf {
    let root = std::env::temp_dir().join(format!(
        "pcbridge-desktop-state-{}-{}",
        std::process::id(),
        now()
    ));
    fs::create_dir_all(&root).unwrap();
    root
}

fn write_grant(state_dir: &Path) {
    let moment = now();
    fs::write(
        state_dir.join("desktop_unlock.json"),
        serde_json::to_vec(&json!({
            "schema_version": 1,
            "grant_id": "desktop-state-contract",
            "revoke_epoch": 0,
            "until": moment + 60.0,
            "hard_until": moment + 60.0,
            "granted": moment,
            "granted_by": "desktop_unlock"
        }))
        .unwrap(),
    )
    .unwrap();
}

struct MutableDesktopState {
    lock: AtomicU8,
    activity: AtomicU8,
    idle_ms: AtomicU64,
}

impl MutableDesktopState {
    fn new(lock: ScreenLockState, activity: ActivityState, idle_ms: u64) -> Self {
        Self {
            lock: AtomicU8::new(lock as u8),
            activity: AtomicU8::new(activity as u8),
            idle_ms: AtomicU64::new(idle_ms),
        }
    }

    fn set_lock(&self, lock: ScreenLockState) {
        self.lock.store(lock as u8, Ordering::Release);
    }

    fn set_activity(&self, activity: ActivityState, idle_ms: u64) {
        self.activity.store(activity as u8, Ordering::Release);
        self.idle_ms.store(idle_ms, Ordering::Release);
    }
}

impl DesktopStateProvider for MutableDesktopState {
    fn screen_lock(&self) -> ScreenLockObservation {
        ScreenLockObservation::new(ScreenLockState::from_u8(self.lock.load(Ordering::Acquire)))
    }

    fn user_activity(&self) -> ActivityObservation {
        let state = ActivityState::from_u8(self.activity.load(Ordering::Acquire));
        ActivityObservation::new(
            state,
            (state == ActivityState::Known).then(|| self.idle_ms.load(Ordering::Acquire)),
        )
    }

    fn wait_for_lock_change(&self, timeout: Duration) -> ScreenLockObservation {
        thread::sleep(timeout);
        self.screen_lock()
    }
}

struct SlowDesktopState;

impl DesktopStateProvider for SlowDesktopState {
    fn screen_lock(&self) -> ScreenLockObservation {
        ScreenLockObservation::new(ScreenLockState::KnownUnlocked)
    }

    fn user_activity(&self) -> ActivityObservation {
        ActivityObservation::new(ActivityState::Known, Some(120_000))
    }

    fn wait_for_lock_change(&self, _timeout: Duration) -> ScreenLockObservation {
        thread::sleep(Duration::from_millis(500));
        self.screen_lock()
    }
}

#[test]
fn force_bypasses_only_activity() {
    let root = fixture_root();
    write_grant(&root);
    let state = Arc::new(MutableDesktopState::new(
        ScreenLockState::KnownUnlocked,
        ActivityState::Unknown,
        0,
    ));
    let lifecycle = Lifecycle::start_with_provider(&root, state.clone()).unwrap();

    assert_eq!(
        lifecycle.validate_write_now(false, 60_000),
        Err(LifecycleFailure::ActivityUnknown)
    );
    assert_eq!(lifecycle.validate_write_now(true, 60_000), Ok(()));

    state.set_lock(ScreenLockState::KnownLocked);
    assert_eq!(
        lifecycle.validate_write_now(true, 60_000),
        Err(LifecycleFailure::ScreenLocked)
    );
    state.set_lock(ScreenLockState::Unknown);
    assert_eq!(
        lifecycle.validate_write_now(true, 60_000),
        Err(LifecycleFailure::LockStateUnknown)
    );

    state.set_lock(ScreenLockState::KnownUnlocked);
    state.set_activity(ActivityState::Known, 1_000);
    assert_eq!(
        lifecycle.validate_write_now(false, 60_000),
        Err(LifecycleFailure::UserActive)
    );
    assert_eq!(lifecycle.validate_write_now(true, 60_000), Ok(()));
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn lock_and_connection_loss_close_active_resources() {
    for blocked in [ScreenLockState::KnownLocked, ScreenLockState::Unknown] {
        let root = fixture_root();
        write_grant(&root);
        let state = Arc::new(MutableDesktopState::new(
            ScreenLockState::KnownUnlocked,
            ActivityState::Known,
            120_000,
        ));
        let lifecycle = Lifecycle::start_with_provider(&root, state.clone()).unwrap();
        lifecycle.open_test_resource().unwrap();

        let started = Instant::now();
        state.set_lock(blocked);
        while lifecycle.test_resource_is_open() {
            assert!(started.elapsed() < Duration::from_secs(1));
            thread::sleep(Duration::from_millis(10));
        }

        assert!(started.elapsed() <= Duration::from_millis(250));
        fs::remove_dir_all(root).unwrap();
    }
}

#[test]
fn stalled_desktop_observation_does_not_delay_revoke() {
    let root = fixture_root();
    write_grant(&root);
    let lifecycle = Lifecycle::start_with_provider(&root, Arc::new(SlowDesktopState)).unwrap();
    lifecycle.open_test_resource().unwrap();
    thread::sleep(Duration::from_millis(20));

    fs::remove_file(root.join("desktop_unlock.json")).unwrap();
    let started = Instant::now();
    while lifecycle.test_resource_is_open() {
        assert!(started.elapsed() < Duration::from_millis(250));
        thread::sleep(Duration::from_millis(10));
    }

    fs::remove_dir_all(root).unwrap();
}
