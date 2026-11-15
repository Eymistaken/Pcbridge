//! The monitor layout on Windows, as the shared `DisplayState`.
//!
//! The process is made Per-Monitor-V2 DPI aware first, so every rectangle
//! is in physical pixels and the layout is the physical one (one canvas unit
//! is one framebuffer pixel, as in Mutter's physical mode); the scale is the
//! monitor's effective DPI over 96. `pcbridge_core::display::resolve` then
//! orders, numbers and translates the table exactly as on Linux. Measured in
//! W0: one 1280x800 monitor at 96 DPI on the test VM.

use pcbridge_core::display::{DisplayMode, LayoutMode, LogicalMonitor, PhysicalMonitor};
use pcbridge_core::{DisplayState, Monitor};
use windows::Win32::Foundation::{BOOL, LPARAM, RECT};
use windows::Win32::Graphics::Gdi::{
    EnumDisplayMonitors, GetMonitorInfoW, HDC, HMONITOR, MONITORINFO, MONITORINFOEXW,
};
use windows::Win32::UI::HiDpi::{
    DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2, GetDpiForMonitor, MDT_EFFECTIVE_DPI,
    SetProcessDpiAwarenessContext,
};
use windows::Win32::UI::WindowsAndMessaging::MONITORINFOF_PRIMARY;

/// Make the process Per-Monitor-V2 aware. Once per process; a second call
/// fails harmlessly with "access denied", which is ignored.
pub fn ensure_dpi_awareness() {
    // SAFETY: takes a constant context handle and changes only this process.
    let _ = unsafe { SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) };
}

struct Found {
    device: String,
    rect: RECT,
    primary: bool,
    dpi: u32,
}

unsafe extern "system" fn collect(
    monitor: HMONITOR,
    _hdc: HDC,
    _clip: *mut RECT,
    data: LPARAM,
) -> BOOL {
    // SAFETY: `data` is the address of the Vec that `monitors()` passed in,
    // alive for the whole enumeration; Windows calls back on this thread.
    let found = unsafe { &mut *(data.0 as *mut Vec<Found>) };
    let mut info = MONITORINFOEXW::default();
    info.monitorInfo.cbSize = std::mem::size_of::<MONITORINFOEXW>() as u32;
    // SAFETY: `info` is a MONITORINFOEXW with cbSize set, as the API requires.
    let ok = unsafe { GetMonitorInfoW(monitor, (&raw mut info).cast::<MONITORINFO>()) };
    if !ok.as_bool() {
        return BOOL::from(true);
    }
    let (mut dpi_x, mut dpi_y) = (96u32, 96u32);
    // SAFETY: two out-parameters this frame owns.
    let _ = unsafe { GetDpiForMonitor(monitor, MDT_EFFECTIVE_DPI, &mut dpi_x, &mut dpi_y) };
    let end = info
        .szDevice
        .iter()
        .position(|&c| c == 0)
        .unwrap_or(info.szDevice.len());
    found.push(Found {
        device: String::from_utf16_lossy(&info.szDevice[..end]),
        rect: info.monitorInfo.rcMonitor,
        primary: info.monitorInfo.dwFlags & MONITORINFOF_PRIMARY != 0,
        dpi: dpi_x,
    });
    BOOL::from(true)
}

/// The current layout as the platform-neutral state.
pub fn state() -> Result<DisplayState, String> {
    ensure_dpi_awareness();
    let mut found: Vec<Found> = Vec::new();
    // SAFETY: the callback only touches `found`, which outlives the call.
    let ok =
        unsafe { EnumDisplayMonitors(None, None, Some(collect), LPARAM(&raw mut found as isize)) };
    if !ok.as_bool() || found.is_empty() {
        return Err("EnumDisplayMonitors found no monitor".to_owned());
    }
    let mut state = DisplayState {
        layout_mode: LayoutMode::Physical,
        ..DisplayState::default()
    };
    for monitor in found {
        let width = (monitor.rect.right - monitor.rect.left).max(0) as u32;
        let height = (monitor.rect.bottom - monitor.rect.top).max(0) as u32;
        state.physical.push(PhysicalMonitor {
            connector: monitor.device.clone(),
            vendor: String::new(),
            product: monitor.device.clone(),
            serial: monitor.device.clone(),
            display_name: monitor.device.clone(),
            modes: vec![DisplayMode {
                width,
                height,
                is_current: true,
            }],
        });
        state.logical.push(LogicalMonitor {
            x: monitor.rect.left,
            y: monitor.rect.top,
            scale: f64::from(monitor.dpi) / 96.0,
            transform: 0,
            primary: monitor.primary,
            connectors: vec![monitor.device],
        });
    }
    Ok(state)
}

/// The ordered monitor table, the same `resolve` Linux uses.
pub fn monitors() -> Result<Vec<Monitor>, String> {
    pcbridge_core::display::resolve(&state()?).map_err(|error| error.to_string())
}
