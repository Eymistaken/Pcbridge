//! `pcbridge-native glow-watch` on Windows: the grant frame and the tray icon.
//!
//! While a grant holds, every monitor gets the frame the other systems
//! draw (`crate::glow::appearance`: four white strips fading inward,
//! breathing): one click-through, always-on-top layered window per strip,
//! drawn with per-pixel alpha. The frame is left out of screen captures
//! (`WDA_EXCLUDEFROMCAPTURE`), so screenshots show the screen, not the
//! frame. A tray icon says desktop control is on and offers "Lock now",
//! which runs `<program> lock` (pcbridge's own kill switch).
//!
//! The process ends when the grant is revoked, expires or is replaced, the
//! session is not known unlocked, or the process that started it (the
//! daemon) ends. While it runs it keeps `glow.json` in the state directory
//! current, the record the gate reads to know the frame is shown.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use pcbridge_core::{DesktopLease, LEASE_STATE_FILE, LeaseToken};
use serde_json::json;
use windows::Win32::Foundation::{
    COLORREF, CloseHandle, HANDLE, HINSTANCE, HWND, LPARAM, LRESULT, POINT, SIZE, WAIT_OBJECT_0,
    WPARAM,
};
use windows::Win32::Graphics::Gdi::{
    AC_SRC_ALPHA, AC_SRC_OVER, BI_RGB, BITMAPINFO, BITMAPINFOHEADER, BLENDFUNCTION,
    CreateCompatibleDC, CreateDIBSection, DIB_RGB_COLORS, DeleteDC, DeleteObject, GetDC, HBITMAP,
    HDC, ReleaseDC, SelectObject,
};
use windows::Win32::System::Diagnostics::ToolHelp::{
    CreateToolhelp32Snapshot, PROCESSENTRY32W, Process32FirstW, Process32NextW, TH32CS_SNAPPROCESS,
};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::System::Threading::{
    OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_SYNCHRONIZE, WaitForSingleObject,
};
use windows::Win32::UI::Shell::{
    NIF_ICON, NIF_MESSAGE, NIF_TIP, NIM_ADD, NIM_DELETE, NOTIFYICONDATAW, Shell_NotifyIconW,
};
use windows::Win32::UI::WindowsAndMessaging::{
    AppendMenuW, CreatePopupMenu, CreateWindowExW, DefWindowProcW, DestroyMenu, DestroyWindow,
    DispatchMessageW, GetCursorPos, GetSystemMetrics, IDI_SHIELD, LoadIconW, MF_STRING, MSG,
    PM_REMOVE, PeekMessageW, RegisterClassExW, SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN,
    SW_SHOWNOACTIVATE, SetForegroundWindow, SetWindowDisplayAffinity, ShowWindow, TPM_RETURNCMD,
    TPM_RIGHTBUTTON, TrackPopupMenu, TranslateMessage, ULW_ALPHA, UpdateLayeredWindow,
    WDA_EXCLUDEFROMCAPTURE, WM_APP, WM_LBUTTONUP, WM_RBUTTONUP, WNDCLASSEXW, WS_EX_LAYERED,
    WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_POPUP,
};
use windows::core::{PCWSTR, w};

use super::desktop_state::{LockState, lock_state};
use super::display;
use super::idle::writer_start_ticks;
use crate::glow::appearance::{Edge, breath, depth, fade_in, fade_out, pixels};

pub const STATE_FILE: &str = "glow.json";
const RECORD_VERSION: u32 = 2;
const TICK: Duration = Duration::from_millis(50);
const TRAY_MESSAGE: u32 = WM_APP + 1;
const LOCK_COMMAND: usize = 1;

static LOCK_REQUESTED: AtomicBool = AtomicBool::new(false);

fn unix_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |elapsed| elapsed.as_millis() as u64)
}

fn lease_valid(directory: &Path, token: &LeaseToken) -> bool {
    DesktopLease::read(&directory.join(LEASE_STATE_FILE))
        .is_ok_and(|lease| lease.validates_at(token, unix_ms() as f64 / 1000.0))
}

/// The parent process and its creation time: the frame ends with it.
struct Owner {
    pid: u32,
    handle: HANDLE,
}

impl Owner {
    fn current() -> Option<Self> {
        let me = std::process::id();
        // SAFETY: a process snapshot read entry by entry, then closed.
        let parent = unsafe {
            let snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0).ok()?;
            let mut entry = PROCESSENTRY32W {
                dwSize: std::mem::size_of::<PROCESSENTRY32W>() as u32,
                ..Default::default()
            };
            let mut parent = None;
            let mut more = Process32FirstW(snapshot, &mut entry).is_ok();
            while more {
                if entry.th32ProcessID == me {
                    parent = Some(entry.th32ParentProcessID);
                    break;
                }
                more = Process32NextW(snapshot, &mut entry).is_ok();
            }
            let _ = CloseHandle(snapshot);
            parent?
        };
        // SAFETY: a handle closed in Drop.
        let handle = unsafe {
            OpenProcess(
                PROCESS_SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION,
                false,
                parent,
            )
        }
        .ok()?;
        Some(Self {
            pid: parent,
            handle,
        })
    }

    fn alive(&self) -> bool {
        // SAFETY: a zero-timeout wait on a handle this value owns.
        unsafe { WaitForSingleObject(self.handle, 0) != WAIT_OBJECT_0 }
    }

    fn start_ticks(&self) -> u64 {
        use windows::Win32::Foundation::FILETIME;
        use windows::Win32::System::Threading::GetProcessTimes;
        let (mut created, mut exited, mut kernel, mut user) = (
            FILETIME::default(),
            FILETIME::default(),
            FILETIME::default(),
            FILETIME::default(),
        );
        // SAFETY: four out-parameters for a process handle this value owns.
        if unsafe {
            GetProcessTimes(
                self.handle,
                &mut created,
                &mut exited,
                &mut kernel,
                &mut user,
            )
        }
        .is_err()
        {
            return 0;
        }
        (u64::from(created.dwHighDateTime) << 32) | u64::from(created.dwLowDateTime)
    }
}

impl Drop for Owner {
    fn drop(&mut self) {
        // SAFETY: the handle this value opened.
        let _ = unsafe { CloseHandle(self.handle) };
    }
}

unsafe extern "system" fn frame_proc(hwnd: HWND, message: u32, w: WPARAM, l: LPARAM) -> LRESULT {
    // SAFETY: the default handling for a window this module created.
    unsafe { DefWindowProcW(hwnd, message, w, l) }
}

unsafe extern "system" fn tray_proc(hwnd: HWND, message: u32, w: WPARAM, l: LPARAM) -> LRESULT {
    if message == TRAY_MESSAGE {
        let event = (l.0 & 0xffff) as u32;
        if event == WM_RBUTTONUP || event == WM_LBUTTONUP {
            show_menu(hwnd);
        }
        return LRESULT(0);
    }
    // SAFETY: the default handling for a window this module created.
    unsafe { DefWindowProcW(hwnd, message, w, l) }
}

/// The tray icon's menu, with its one item.
fn show_menu(hwnd: HWND) {
    // SAFETY: a menu created, shown and destroyed here, owned by `hwnd`.
    unsafe {
        let Ok(menu) = CreatePopupMenu() else {
            return;
        };
        let _ = AppendMenuW(menu, MF_STRING, LOCK_COMMAND, w!("Lock now (pcbridge)"));
        let mut point = POINT::default();
        let _ = GetCursorPos(&mut point);
        // A menu closes when it loses the focus only if its window is in
        // front (the documented Shell_NotifyIcon pattern).
        let _ = SetForegroundWindow(hwnd);
        let chosen = TrackPopupMenu(
            menu,
            TPM_RETURNCMD | TPM_RIGHTBUTTON,
            point.x,
            point.y,
            0,
            hwnd,
            None,
        );
        if chosen.0 as usize == LOCK_COMMAND {
            LOCK_REQUESTED.store(true, Ordering::Release);
        }
        let _ = DestroyMenu(menu);
    }
}

fn instance() -> HINSTANCE {
    // SAFETY: this process's own module handle.
    unsafe { GetModuleHandleW(None) }.map_or_else(|_| HINSTANCE::default(), |module| module.into())
}

fn register(name: PCWSTR, procedure: windows::Win32::UI::WindowsAndMessaging::WNDPROC) {
    let class = WNDCLASSEXW {
        cbSize: std::mem::size_of::<WNDCLASSEXW>() as u32,
        lpfnWndProc: procedure,
        hInstance: instance(),
        lpszClassName: name,
        ..Default::default()
    };
    // SAFETY: a class description whose strings are static.
    unsafe { RegisterClassExW(&class) };
}

/// One strip of the frame: its window and where it sits.
struct Strip {
    hwnd: HWND,
    edge: Edge,
    x: i32,
    y: i32,
    width: u32,
    height: u32,
}

impl Strip {
    fn create(edge: Edge, x: i32, y: i32, width: u32, height: u32) -> Option<Self> {
        // SAFETY: a window of a class this module registered; it is shown
        // without activation and never takes input (WS_EX_TRANSPARENT).
        let hwnd = unsafe {
            CreateWindowExW(
                WS_EX_LAYERED
                    | WS_EX_TRANSPARENT
                    | WS_EX_TOPMOST
                    | WS_EX_TOOLWINDOW
                    | WS_EX_NOACTIVATE,
                w!("pcbridgeFrame"),
                w!("pcbridge frame"),
                WS_POPUP,
                x,
                y,
                width as i32,
                height as i32,
                HWND::default(),
                None,
                instance(),
                None,
            )
        }
        .ok()?;
        // SAFETY: plain calls on the window just created.
        unsafe {
            let _ = SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE);
        }
        Some(Self {
            hwnd,
            edge,
            x,
            y,
            width,
            height,
        })
    }

    /// Draw the strip at `opacity` (fade) and `breathing` (depth factor).
    fn draw(&self, opacity: f64, breathing: f64) -> bool {
        let bytes = pixels(self.edge, self.width, self.height, opacity, breathing);
        let header = BITMAPINFOHEADER {
            biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
            biWidth: self.width as i32,
            biHeight: -(self.height as i32),
            biPlanes: 1,
            biBitCount: 32,
            biCompression: BI_RGB.0,
            ..Default::default()
        };
        let info = BITMAPINFO {
            bmiHeader: header,
            ..Default::default()
        };
        // SAFETY: a DIB section of exactly `bytes.len()` bytes is filled and
        // handed to UpdateLayeredWindow; every GDI object is released.
        unsafe {
            let screen: HDC = GetDC(HWND::default());
            let memory = CreateCompatibleDC(screen);
            let mut bits: *mut core::ffi::c_void = std::ptr::null_mut();
            let bitmap: HBITMAP =
                match CreateDIBSection(memory, &info, DIB_RGB_COLORS, &mut bits, None, 0) {
                    Ok(bitmap) if !bits.is_null() => bitmap,
                    _ => {
                        let _ = DeleteDC(memory);
                        ReleaseDC(HWND::default(), screen);
                        return false;
                    }
                };
            std::ptr::copy_nonoverlapping(bytes.as_ptr(), bits.cast::<u8>(), bytes.len());
            let previous = SelectObject(memory, bitmap);
            let position = POINT {
                x: self.x,
                y: self.y,
            };
            let size = SIZE {
                cx: self.width as i32,
                cy: self.height as i32,
            };
            let source = POINT::default();
            let blend = BLENDFUNCTION {
                BlendOp: AC_SRC_OVER as u8,
                BlendFlags: 0,
                SourceConstantAlpha: 255,
                AlphaFormat: AC_SRC_ALPHA as u8,
            };
            let drawn = UpdateLayeredWindow(
                self.hwnd,
                screen,
                Some(&position),
                Some(&size),
                memory,
                Some(&source),
                COLORREF(0),
                Some(&blend),
                ULW_ALPHA,
            )
            .is_ok();
            SelectObject(memory, previous);
            let _ = DeleteObject(bitmap);
            let _ = DeleteDC(memory);
            ReleaseDC(HWND::default(), screen);
            if drawn {
                let _ = ShowWindow(self.hwnd, SW_SHOWNOACTIVATE);
            }
            drawn
        }
    }
}

impl Drop for Strip {
    fn drop(&mut self) {
        // SAFETY: the window this value created.
        let _ = unsafe { DestroyWindow(self.hwnd) };
    }
}

/// The frame on every monitor, in virtual-screen coordinates.
struct Frame {
    strips: Vec<Strip>,
    topology_id: String,
    outputs: Vec<String>,
}

impl Frame {
    fn build() -> Result<Self, String> {
        let monitors = display::monitors()?;
        // SAFETY: plain metric reads.
        let (left, top) = unsafe {
            (
                GetSystemMetrics(SM_XVIRTUALSCREEN),
                GetSystemMetrics(SM_YVIRTUALSCREEN),
            )
        };
        let mut strips = Vec::new();
        for monitor in &monitors {
            let (x, y) = (monitor.x + left, monitor.y + top);
            let (width, height) = (monitor.width, monitor.height);
            let thick = depth(width, height).min(width).min(height);
            let pieces = [
                (Edge::Top, x, y, width, thick),
                (Edge::Bottom, x, y + (height - thick) as i32, width, thick),
                (Edge::Left, x, y, thick, height),
                (Edge::Right, x + (width - thick) as i32, y, thick, height),
            ];
            for (edge, x, y, width, height) in pieces {
                strips.push(
                    Strip::create(edge, x, y, width, height)
                        .ok_or("a frame window could not be created")?,
                );
            }
        }
        Ok(Self {
            strips,
            topology_id: pcbridge_core::display::topology_id(&monitors),
            outputs: monitors.iter().map(|m| m.connector.clone()).collect(),
        })
    }

    fn draw(&self, opacity: f64, breathing: f64) -> bool {
        self.strips
            .iter()
            .all(|strip| strip.draw(opacity, breathing))
    }
}

/// The tray icon, removed when dropped.
struct Tray {
    data: NOTIFYICONDATAW,
}

impl Tray {
    fn add() -> Option<Self> {
        // SAFETY: a hidden window of a class this module registered; it
        // only receives the icon's messages.
        let hwnd = unsafe {
            CreateWindowExW(
                WS_EX_TOOLWINDOW,
                w!("pcbridgeTray"),
                w!("pcbridge"),
                WS_POPUP,
                0,
                0,
                0,
                0,
                HWND::default(),
                None,
                instance(),
                None,
            )
        }
        .ok()?;
        let mut data = NOTIFYICONDATAW {
            cbSize: std::mem::size_of::<NOTIFYICONDATAW>() as u32,
            hWnd: hwnd,
            uID: 1,
            uFlags: NIF_ICON | NIF_MESSAGE | NIF_TIP,
            uCallbackMessage: TRAY_MESSAGE,
            // SAFETY: a system icon.
            hIcon: unsafe { LoadIconW(None, IDI_SHIELD) }.unwrap_or_default(),
            ..Default::default()
        };
        let tip: Vec<u16> = "pcbridge: desktop control is on".encode_utf16().collect();
        data.szTip[..tip.len()].copy_from_slice(&tip);
        // SAFETY: a fully initialized structure.
        unsafe { Shell_NotifyIconW(NIM_ADD, &data) }
            .as_bool()
            .then_some(Self { data })
    }
}

impl Drop for Tray {
    fn drop(&mut self) {
        // SAFETY: removes the icon this value added, then its window.
        unsafe {
            let _ = Shell_NotifyIconW(NIM_DELETE, &self.data);
            let _ = DestroyWindow(self.data.hWnd);
        }
    }
}

fn pump() {
    let mut message = MSG::default();
    // SAFETY: a message loop on the thread that owns the windows.
    unsafe {
        while PeekMessageW(&mut message, None, 0, 0, PM_REMOVE).as_bool() {
            let _ = TranslateMessage(&message);
            DispatchMessageW(&message);
        }
    }
}

struct Record<'a> {
    directory: &'a Path,
    token: &'a LeaseToken,
    pid: u32,
    started: u64,
    owner_pid: u32,
    owner_started: u64,
    session: u32,
}

impl Record<'_> {
    fn write(&self, frame: &Frame, ready: bool) {
        let record = json!({
            "version": RECORD_VERSION,
            "ready": ready,
            "pid": self.pid,
            "writer_start_ticks": self.started,
            "owner_pid": self.owner_pid,
            "owner_start_ticks": self.owner_started,
            "grant_id": self.token.grant_id(),
            "revoke_epoch": self.token.revoke_epoch(),
            "wayland_display": "",
            "hyprland_instance": "",
            "x11_display": "",
            "windows_session": self.session,
            "topology_id": frame.topology_id,
            "outputs": frame.outputs,
            "strip_count": frame.strips.len(),
            "presented_unix_ms": if ready { unix_ms() } else { 0 },
        });
        let path = self.directory.join(STATE_FILE);
        let temporary = self.directory.join("glow.json.tmp");
        if std::fs::write(&temporary, record.to_string()).is_ok() {
            let _ = std::fs::rename(&temporary, &path);
        }
    }
}

fn session_id() -> u32 {
    let mut session = 0u32;
    // SAFETY: an out-parameter for this process.
    let _ = unsafe {
        windows::Win32::System::RemoteDesktop::ProcessIdToSessionId(
            std::process::id(),
            &mut session,
        )
    };
    session
}

/// Own the frame and the tray icon until the grant ends. `lock_program`
/// is pcbridge's executable, run as `<lock_program> lock` from the menu.
pub fn watch(
    directory: &Path,
    token: LeaseToken,
    lock_program: Option<PathBuf>,
) -> Result<(), String> {
    if !lease_valid(directory, &token) {
        return Err("No matching active frame lease".into());
    }
    if lock_state() != LockState::KnownUnlocked {
        return Err("The session is not known unlocked".into());
    }
    display::ensure_dpi_awareness();
    let owner = Owner::current().ok_or("the process that started the frame is gone")?;
    register(w!("pcbridgeFrame"), Some(frame_proc));
    register(w!("pcbridgeTray"), Some(tray_proc));
    let mut frame = Frame::build()?;
    let tray = Tray::add();
    let record = Record {
        directory,
        token: &token,
        pid: std::process::id(),
        started: writer_start_ticks(),
        owner_pid: owner.pid,
        owner_started: owner.start_ticks(),
        session: session_id(),
    };
    let shown = Instant::now();
    let reason = loop {
        pump();
        if LOCK_REQUESTED.swap(false, Ordering::AcqRel)
            && let Some(program) = lock_program.as_ref()
        {
            let _ = std::process::Command::new(program).arg("lock").spawn();
        }
        if !lease_valid(directory, &token) {
            break "the grant ended";
        }
        if lock_state() != LockState::KnownUnlocked {
            break "the session is not known unlocked";
        }
        if !owner.alive() {
            break "the process that started the frame ended";
        }
        if display::monitors()
            .map(|monitors| pcbridge_core::display::topology_id(&monitors))
            .is_ok_and(|topology| topology != frame.topology_id)
        {
            record.write(&frame, false);
            drop(frame);
            frame = Frame::build()?;
        }
        let elapsed = shown.elapsed().as_secs_f64() * 1000.0;
        let drawn = frame.draw(fade_in(elapsed), breath(elapsed));
        record.write(&frame, drawn);
        std::thread::sleep(TICK);
    };
    record.write(&frame, false);
    let closing = Instant::now();
    while closing.elapsed() < Duration::from_millis(500) {
        let _ = frame.draw(fade_out(closing.elapsed().as_secs_f64() * 1000.0), 1.0);
        pump();
        std::thread::sleep(TICK);
    }
    drop(tray);
    drop(frame);
    let _ = std::fs::remove_file(directory.join(STATE_FILE));
    eprintln!("pcbridge-native glow-watch: {reason}");
    Ok(())
}
