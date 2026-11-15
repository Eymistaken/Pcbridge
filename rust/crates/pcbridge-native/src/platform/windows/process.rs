//! Processes behind windows: their names and whether they run above this
//! helper's integrity level.
//!
//! UIPI drops input an unelevated process sends to an elevated window
//! without an error: `SendInput` reports every event inserted (measured in
//! W0, 46 of 46, while Notepad showed nothing). So a write first checks
//! where it would land and refuses an elevated target with a reason.

use windows::Win32::Foundation::{CloseHandle, HANDLE, HWND, POINT};
use windows::Win32::Security::{
    GetSidSubAuthority, GetSidSubAuthorityCount, GetTokenInformation, TOKEN_MANDATORY_LABEL,
    TOKEN_QUERY, TokenIntegrityLevel,
};
use windows::Win32::System::Threading::{
    GetCurrentProcess, OpenProcess, OpenProcessToken, PROCESS_NAME_WIN32,
    PROCESS_QUERY_LIMITED_INFORMATION, QueryFullProcessImageNameW,
};
use windows::Win32::UI::WindowsAndMessaging::{
    GA_ROOT, GetAncestor, GetCursorPos, GetForegroundWindow, GetWindowThreadProcessId,
    WindowFromPoint,
};

struct Handle(HANDLE);

impl Drop for Handle {
    fn drop(&mut self) {
        // SAFETY: a handle this value opened.
        let _ = unsafe { CloseHandle(self.0) };
    }
}

fn open(pid: u32) -> Option<Handle> {
    // SAFETY: opens a handle closed by `Handle`'s Drop.
    unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) }
        .ok()
        .map(Handle)
}

/// The executable's name without `.exe` ("Notepad"), as an application's
/// name in a dump.
#[must_use]
pub fn name(pid: u32) -> Option<String> {
    let process = open(pid)?;
    let mut buffer = [0u16; 1024];
    let mut length = buffer.len() as u32;
    // SAFETY: a buffer and its length in characters.
    unsafe {
        QueryFullProcessImageNameW(
            process.0,
            PROCESS_NAME_WIN32,
            windows::core::PWSTR(buffer.as_mut_ptr()),
            &mut length,
        )
    }
    .ok()?;
    let path = String::from_utf16_lossy(&buffer[..length as usize]);
    let file = path.rsplit('\\').next().unwrap_or(&path);
    Some(
        file.strip_suffix(".exe")
            .or_else(|| file.strip_suffix(".EXE"))
            .unwrap_or(file)
            .to_owned(),
    )
}

/// The integrity level's RID (0x2000 medium, 0x3000 high) of a process
/// token, or None when the token cannot be read.
fn token_integrity(process: HANDLE) -> Option<u32> {
    let mut token = HANDLE::default();
    // SAFETY: an out-parameter; the token is closed below.
    unsafe { OpenProcessToken(process, TOKEN_QUERY, &mut token) }.ok()?;
    let token = Handle(token);
    let mut needed = 0u32;
    // SAFETY: a size query with no buffer.
    let _ = unsafe { GetTokenInformation(token.0, TokenIntegrityLevel, None, 0, &mut needed) };
    let mut buffer = vec![0u8; needed as usize];
    // SAFETY: a buffer of the size Windows asked for.
    unsafe {
        GetTokenInformation(
            token.0,
            TokenIntegrityLevel,
            Some(buffer.as_mut_ptr().cast()),
            needed,
            &mut needed,
        )
    }
    .ok()?;
    // SAFETY: the buffer holds a TOKEN_MANDATORY_LABEL whose SID points
    // into the same buffer; the last sub-authority is the level.
    unsafe {
        let label = &*buffer.as_ptr().cast::<TOKEN_MANDATORY_LABEL>();
        let count = *GetSidSubAuthorityCount(label.Label.Sid);
        Some(*GetSidSubAuthority(
            label.Label.Sid,
            u32::from(count.saturating_sub(1)),
        ))
    }
}

/// Whether `pid` runs above this helper. A process whose token cannot be
/// read counts as above: an unelevated process is refused exactly the
/// tokens of elevated ones, and an unknown target is not written to.
#[must_use]
pub fn runs_above_us(pid: u32) -> bool {
    // SAFETY: the pseudo handle of this process needs no closing.
    let ours = token_integrity(unsafe { GetCurrentProcess() }).unwrap_or(0x2000);
    let Some(process) = open(pid) else {
        return true;
    };
    token_integrity(process.0).is_none_or(|theirs| theirs > ours)
}

/// The process that owns a window.
#[must_use]
pub fn window_pid(hwnd: HWND) -> Option<u32> {
    if hwnd.0.is_null() {
        return None;
    }
    let mut pid = 0u32;
    // SAFETY: an out-parameter this frame owns.
    unsafe { GetWindowThreadProcessId(hwnd, Some(&mut pid)) };
    (pid != 0).then_some(pid)
}

/// The process of the window in front, which keys go to.
#[must_use]
pub fn foreground_pid() -> Option<u32> {
    // SAFETY: a plain read.
    window_pid(unsafe { GetForegroundWindow() })
}

/// The process of the top-level window at a canvas point (the virtual
/// screen moved to start at 0,0).
#[must_use]
pub fn pid_at(x: i32, y: i32) -> Option<u32> {
    use windows::Win32::UI::WindowsAndMessaging::{
        GetSystemMetrics, SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN,
    };
    // SAFETY: plain metric reads, then reads of the handles Windows returned.
    unsafe {
        let point = POINT {
            x: x + GetSystemMetrics(SM_XVIRTUALSCREEN),
            y: y + GetSystemMetrics(SM_YVIRTUALSCREEN),
        };
        window_pid(GetAncestor(WindowFromPoint(point), GA_ROOT))
    }
}

/// The process of the top-level window under the pointer, which a click or
/// the wheel goes to.
#[must_use]
pub fn pid_under_cursor() -> Option<u32> {
    let mut point = POINT::default();
    // SAFETY: an out-parameter this frame owns, then reads of the handles
    // Windows returned.
    unsafe {
        GetCursorPos(&mut point).ok()?;
        window_pid(GetAncestor(WindowFromPoint(point), GA_ROOT))
    }
}
