//! Bringing a top-level window to the front.
//!
//! `SetForegroundWindow` works only for the process that received the last
//! input event. Measured in W0: after a click on the taskbar moved the
//! foreground away the call returned false and nothing changed; after one
//! injected `VK_F24`, a key bound to nothing, the same call succeeded.
//! `SendInput` counts as receiving that input.

use std::time::{Duration, Instant};

use windows::Win32::Foundation::HWND;
use windows::Win32::UI::Input::KeyboardAndMouse::{
    INPUT, INPUT_0, INPUT_KEYBOARD, KEYBD_EVENT_FLAGS, KEYBDINPUT, KEYEVENTF_KEYUP, SendInput,
    VK_F24,
};
use windows::Win32::UI::WindowsAndMessaging::{
    GetForegroundWindow, IsIconic, SW_RESTORE, SetForegroundWindow, ShowWindow,
};

use super::input::INPUT_MARK;

/// How long the foreground may take to follow.
const ACTIVATION_WAIT: Duration = Duration::from_millis(1_000);
const ACTIVATION_POLL: Duration = Duration::from_millis(25);

fn f24(flags: KEYBD_EVENT_FLAGS) -> INPUT {
    INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: VK_F24,
                wScan: 0,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: INPUT_MARK,
            },
        },
    }
}

/// The window in front, if any.
#[must_use]
pub fn foreground() -> Option<HWND> {
    // SAFETY: a plain read.
    let hwnd = unsafe { GetForegroundWindow() };
    (!hwnd.0.is_null()).then_some(hwnd)
}

/// Restore `hwnd` if it is minimized, bring it to the front, and say
/// whether Windows now reports it in front.
#[must_use]
pub fn activate(hwnd: HWND) -> bool {
    // SAFETY: calls on a window handle Windows listed; a stale handle makes
    // them fail, which the read-back below reports.
    unsafe {
        if IsIconic(hwnd).as_bool() {
            let _ = ShowWindow(hwnd, SW_RESTORE);
        }
        let keys = [f24(KEYBD_EVENT_FLAGS(0)), f24(KEYEVENTF_KEYUP)];
        SendInput(&keys, std::mem::size_of::<INPUT>() as i32);
        let _ = SetForegroundWindow(hwnd);
    }
    let deadline = Instant::now() + ACTIVATION_WAIT;
    loop {
        // SAFETY: a plain read.
        if unsafe { GetForegroundWindow() } == hwnd {
            return true;
        }
        if Instant::now() >= deadline {
            return false;
        }
        std::thread::sleep(ACTIVATION_POLL);
    }
}
