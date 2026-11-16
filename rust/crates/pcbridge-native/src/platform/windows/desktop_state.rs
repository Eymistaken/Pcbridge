//! Whether the user's session is locked, from Remote Desktop Services.
//!
//! `WTSQuerySessionInformationW(WTSSessionInfoEx)` reports the session's
//! lock flag: measured in W0, 1 while unlocked and 0 after
//! `LockWorkStation`, as documented (a hand-made structure had read the
//! wrong field; the crate's typed structure is used here). A failed query is
//! `Unknown`, never "unlocked": the fail-closed rules treat it as a refusal.
//!
//! A second, independent signal confirms it: `OpenInputDesktop` opens the
//! `Default` desktop while the session is unlocked and fails with access
//! denied while the lock screen (the secure desktop) has the input (also
//! measured in W0). Only when both agree is the state known.

use windows::Win32::System::RemoteDesktop::{
    WTS_CURRENT_SERVER_HANDLE, WTS_CURRENT_SESSION, WTSFreeMemory, WTSINFOEXW,
    WTSQuerySessionInformationW, WTSSessionInfoEx,
};
use windows::Win32::System::StationsAndDesktops::{
    CloseDesktop, DESKTOP_ACCESS_FLAGS, DESKTOP_CONTROL_FLAGS, OpenInputDesktop,
};
use windows::core::PWSTR;

const WTS_SESSIONSTATE_LOCK: i32 = 0;
const WTS_SESSIONSTATE_UNLOCK: i32 = 1;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum LockState {
    KnownLocked,
    KnownUnlocked,
    Unknown,
}

/// Both signals; they must agree.
#[must_use]
pub fn lock_state() -> LockState {
    match (session_lock_state(), input_desktop_open()) {
        (LockState::KnownUnlocked, true) => LockState::KnownUnlocked,
        (LockState::KnownLocked, false) => LockState::KnownLocked,
        _ => LockState::Unknown,
    }
}

/// Whether this session's input desktop opens for this process: it does
/// on the user's desktop and not on the lock screen.
fn input_desktop_open() -> bool {
    // SAFETY: opens a desktop handle with read access only
    // and closes it at once.
    unsafe {
        // DESKTOP_READOBJECTS, the access W0 measured with.
        match OpenInputDesktop(
            DESKTOP_CONTROL_FLAGS(0),
            false,
            DESKTOP_ACCESS_FLAGS(0x0001),
        ) {
            Ok(desktop) => {
                let _ = CloseDesktop(desktop);
                true
            }
            Err(_) => false,
        }
    }
}

/// The session flag from Remote Desktop Services alone.
#[must_use]
pub fn session_lock_state() -> LockState {
    let mut buffer = PWSTR::null();
    let mut bytes = 0u32;
    // SAFETY: two out-parameters this frame owns; the buffer is freed below.
    let ok = unsafe {
        WTSQuerySessionInformationW(
            WTS_CURRENT_SERVER_HANDLE,
            WTS_CURRENT_SESSION,
            WTSSessionInfoEx,
            &mut buffer,
            &mut bytes,
        )
    };
    if ok.is_err() || buffer.is_null() || (bytes as usize) < std::mem::size_of::<WTSINFOEXW>() {
        return LockState::Unknown;
    }
    // SAFETY: Windows returned at least one WTSINFOEXW at `buffer`.
    let info = unsafe { &*buffer.0.cast::<WTSINFOEXW>() };
    let state = if info.Level == 1 {
        // SAFETY: level 1 says the union holds WTSINFOEX_LEVEL1_W.
        match unsafe { info.Data.WTSInfoExLevel1.SessionFlags } {
            WTS_SESSIONSTATE_LOCK => LockState::KnownLocked,
            WTS_SESSIONSTATE_UNLOCK => LockState::KnownUnlocked,
            _ => LockState::Unknown,
        }
    } else {
        LockState::Unknown
    };
    // SAFETY: the buffer came from WTSQuerySessionInformationW.
    unsafe { WTSFreeMemory(buffer.0.cast()) };
    state
}
