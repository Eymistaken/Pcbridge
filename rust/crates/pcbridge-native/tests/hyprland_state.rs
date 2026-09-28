//! Unknown lock replies must never turn into an unlocked observation.

use pcbridge_native::platform::linux::desktop_state::{ScreenLockState, lock_from_hyprland_reply};

#[test]
fn compositor_lock_reply_requires_an_actual_boolean() {
    for (raw, expected) in [
        (
            serde_json::json!({"locked":true}),
            ScreenLockState::KnownLocked,
        ),
        (
            serde_json::json!({"locked":false}),
            ScreenLockState::KnownUnlocked,
        ),
        (
            serde_json::json!({"locked":"false"}),
            ScreenLockState::Unknown,
        ),
        (serde_json::json!({"locked":0}), ScreenLockState::Unknown),
        (serde_json::json!({}), ScreenLockState::Unknown),
        (serde_json::json!(false), ScreenLockState::Unknown),
    ] {
        assert_eq!(lock_from_hyprland_reply(&raw).state, expected, "{raw}");
    }
}
