//! `pcbridge-native idle-watch` on Windows: the user's own input, apart
//! from pcbridge's.
//!
//! `GetLastInputInfo` counts every input, pcbridge's `SendInput` too
//! (measured in W0: idle 998 s, then 0.3 s after a one-pixel move), so it
//! cannot tell whether someone is at the machine while pcbridge works. A
//! low-level keyboard and mouse hook sees each event with its injected flag
//! and `dwExtraInfo` (also measured in W0), so input that carries
//! pcbridge's mark is left out and everything else counts: the hardware,
//! and any other program's injected input.
//!
//! The record has the shape Linux writes (`idle.json`, version 3), with the
//! Wayland and X11 fields empty and the Windows session id added. Windows
//! removes a low-level hook that answers too slowly; if `GetLastInputInfo`
//! ever reports input the hooks did not see, that input is counted as the
//! user's, which is the safe side for the guard.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU32, Ordering};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::json;
use windows::Win32::Foundation::{FILETIME, LPARAM, LRESULT, WPARAM};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::System::RemoteDesktop::ProcessIdToSessionId;
use windows::Win32::System::SystemInformation::GetTickCount;
use windows::Win32::System::Threading::{GetCurrentProcess, GetProcessTimes};
use windows::Win32::UI::Input::KeyboardAndMouse::{GetLastInputInfo, LASTINPUTINFO};
use windows::Win32::UI::WindowsAndMessaging::{
    CallNextHookEx, GetMessageW, HHOOK, KBDLLHOOKSTRUCT, LLKHF_INJECTED, LLMHF_INJECTED, MSG,
    MSLLHOOKSTRUCT, SetWindowsHookExW, WH_KEYBOARD_LL, WH_MOUSE_LL,
};

use super::input::INPUT_MARK;

pub const STATE_FILE: &str = "idle.json";
const RECORD_VERSION: u32 = 3;
const TIMEOUT_MS: u32 = 1_000;
const HEARTBEAT: Duration = Duration::from_millis(250);

/// Tick of the last input that was not pcbridge's.
static LAST_USER: AtomicU32 = AtomicU32::new(0);
/// Tick of the last input of any kind the hooks saw.
static LAST_SEEN: AtomicU32 = AtomicU32::new(0);

fn note(time: u32, ours: bool) {
    LAST_SEEN.store(time, Ordering::Release);
    if !ours {
        LAST_USER.store(time, Ordering::Release);
    }
}

unsafe extern "system" fn keyboard_hook(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    if code >= 0 {
        // SAFETY: for WH_KEYBOARD_LL, lparam points to a KBDLLHOOKSTRUCT.
        let event = unsafe { &*(lparam.0 as *const KBDLLHOOKSTRUCT) };
        let ours = event.flags.0 & LLKHF_INJECTED.0 != 0 && event.dwExtraInfo == INPUT_MARK;
        note(event.time, ours);
    }
    // SAFETY: passes the event on unchanged.
    unsafe { CallNextHookEx(HHOOK::default(), code, wparam, lparam) }
}

unsafe extern "system" fn mouse_hook(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    if code >= 0 {
        // SAFETY: for WH_MOUSE_LL, lparam points to a MSLLHOOKSTRUCT.
        let event = unsafe { &*(lparam.0 as *const MSLLHOOKSTRUCT) };
        let ours = event.flags & LLMHF_INJECTED != 0 && event.dwExtraInfo == INPUT_MARK;
        note(event.time, ours);
    }
    // SAFETY: passes the event on unchanged.
    unsafe { CallNextHookEx(HHOOK::default(), code, wparam, lparam) }
}

/// The hooks and their message loop, on a thread of their own.
fn start_hooks() -> Result<(), String> {
    let (ready, started) = std::sync::mpsc::channel();
    std::thread::Builder::new()
        .name("pcbridge-native-idle-hooks".to_owned())
        .spawn(move || {
            // SAFETY: hooks for this process's module, removed when the
            // process ends; the loop below dispatches their calls.
            let hooks = unsafe {
                let module = GetModuleHandleW(None).map_err(|error| error.to_string());
                module.and_then(|module| {
                    let keyboard =
                        SetWindowsHookExW(WH_KEYBOARD_LL, Some(keyboard_hook), module, 0);
                    let mouse = SetWindowsHookExW(WH_MOUSE_LL, Some(mouse_hook), module, 0);
                    keyboard
                        .and(mouse)
                        .map(|_| ())
                        .map_err(|error| error.to_string())
                })
            };
            let failed = hooks.is_err();
            let _ = ready.send(hooks);
            if failed {
                return;
            }
            let mut message = MSG::default();
            // SAFETY: a message loop on the thread that owns the hooks.
            while unsafe { GetMessageW(&mut message, None, 0, 0) }.as_bool() {}
        })
        .map_err(|error| error.to_string())?;
    started
        .recv_timeout(Duration::from_secs(5))
        .map_err(|_| "the input hooks did not start".to_owned())?
}

fn last_input_tick() -> Option<u32> {
    let mut info = LASTINPUTINFO {
        cbSize: std::mem::size_of::<LASTINPUTINFO>() as u32,
        dwTime: 0,
    };
    // SAFETY: a structure with its size set.
    unsafe { GetLastInputInfo(&mut info) }
        .as_bool()
        .then_some(info.dwTime)
}

fn unix_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |elapsed| elapsed.as_millis() as u64)
}

/// This process's creation time (100 ns since 1601): with the pid, what
/// tells a reader the record's writer is still this process.
#[must_use]
pub fn writer_start_ticks() -> u64 {
    let (mut created, mut exited, mut kernel, mut user) = (
        FILETIME::default(),
        FILETIME::default(),
        FILETIME::default(),
        FILETIME::default(),
    );
    // SAFETY: four out-parameters for the current process.
    let read = unsafe {
        GetProcessTimes(
            GetCurrentProcess(),
            &mut created,
            &mut exited,
            &mut kernel,
            &mut user,
        )
    };
    if read.is_err() {
        return 0;
    }
    (u64::from(created.dwHighDateTime) << 32) | u64::from(created.dwLowDateTime)
}

fn session_id() -> u32 {
    let mut session = 0u32;
    // SAFETY: an out-parameter for this process.
    let _ = unsafe { ProcessIdToSessionId(std::process::id(), &mut session) };
    session
}

/// `%LOCALAPPDATA%\pcbridge\idle.json`, where Python's `idlewatch` reads it.
#[must_use]
pub fn default_path() -> Option<PathBuf> {
    let base = std::env::var_os("LOCALAPPDATA").filter(|value| !value.is_empty())?;
    Some(Path::new(&base).join("pcbridge").join(STATE_FILE))
}

fn write(path: &Path, record: &serde_json::Value) -> std::io::Result<()> {
    let temporary = path.with_extension("json.tmp");
    std::fs::write(&temporary, record.to_string())?;
    std::fs::rename(&temporary, path)
}

/// Keep `path` current until the process is ended. Returns only on an error.
pub fn watch(path: &Path) -> String {
    if let Some(parent) = path.parent()
        && let Err(error) = std::fs::create_dir_all(parent)
    {
        return error.to_string();
    }
    // Until the hooks say otherwise, the last input counts as the user's.
    let start = last_input_tick().unwrap_or_else(|| {
        // SAFETY: a plain read.
        unsafe { GetTickCount() }
    });
    LAST_USER.store(start, Ordering::Release);
    LAST_SEEN.store(start, Ordering::Release);
    if let Err(error) = start_hooks() {
        return error;
    }
    let pid = std::process::id();
    let started = writer_start_ticks();
    let session = session_id();
    loop {
        // SAFETY: a plain read.
        let now_tick = unsafe { GetTickCount() };
        if let Some(input) = last_input_tick() {
            let seen = LAST_SEEN.load(Ordering::Acquire);
            // Input the hooks missed (wrapping tick arithmetic).
            if input.wrapping_sub(seen) as i32 > TIMEOUT_MS as i32 {
                LAST_USER.store(input, Ordering::Release);
                LAST_SEEN.store(input, Ordering::Release);
            }
        }
        let idle_ms = u64::from(now_tick.wrapping_sub(LAST_USER.load(Ordering::Acquire)));
        let now = unix_ms();
        let record = json!({
            "version": RECORD_VERSION,
            "pid": pid,
            "timeout_ms": TIMEOUT_MS,
            "idle": idle_ms >= u64::from(TIMEOUT_MS),
            "since_unix_ms": now.saturating_sub(idle_ms),
            "confirmed_unix_ms": now,
            "writer_start_ticks": started,
            "wayland_display": "",
            "hyprland_instance": "",
            "x11_display": "",
            "windows_session": session,
        });
        if let Err(error) = write(path, &record) {
            // A reader holding the file open makes the rename fail for a
            // moment; only a lasting failure ends the watcher.
            std::thread::sleep(HEARTBEAT);
            if let Err(again) = write(path, &record) {
                return format!("{error}; then {again}");
            }
        }
        std::thread::sleep(HEARTBEAT);
    }
}
