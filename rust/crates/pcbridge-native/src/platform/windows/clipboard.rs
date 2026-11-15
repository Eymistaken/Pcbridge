//! The Windows clipboard as text (`CF_UNICODETEXT`).
//!
//! The protocol's clipboard is bytes with a MIME type; on Windows pcbridge
//! reads and writes text only: a `text/*` write is decoded as UTF-8 and
//! stored as UTF-16, a read returns UTF-8 as `text/plain;charset=utf-8`.
//! Another program can hold the clipboard open for a moment, so opening it
//! is retried for up to `OPEN_WAIT`.

use std::time::{Duration, Instant};

use windows::Win32::Foundation::{GlobalFree, HANDLE, HGLOBAL, HWND};
use windows::Win32::System::DataExchange::{
    CloseClipboard, EmptyClipboard, GetClipboardData, IsClipboardFormatAvailable, OpenClipboard,
    SetClipboardData,
};
use windows::Win32::System::Memory::{
    GMEM_MOVEABLE, GlobalAlloc, GlobalLock, GlobalSize, GlobalUnlock,
};

pub const BACKEND: &str = "windows.clipboard";
pub const TEXT_MIME: &str = "text/plain;charset=utf-8";

const CF_UNICODETEXT: u32 = 13;
const OPEN_WAIT: Duration = Duration::from_millis(500);
/// The protocol's payload limit for clipboard content.
pub const MAX_BYTES: usize = 128 * 1024 * 1024;

#[derive(Debug, thiserror::Error)]
pub enum ClipboardError {
    #[error("another program holds the clipboard open")]
    Busy,
    #[error("only text can be written to the Windows clipboard, not '{0}'")]
    NotText(String),
    #[error("the text is not valid UTF-8")]
    InvalidUtf8,
    #[error("the content is larger than the 128 MiB limit")]
    TooLarge,
    #[error("the clipboard call failed: {0}")]
    Failed(String),
}

/// The clipboard, open for the life of this value.
struct Open;

impl Open {
    fn acquire() -> Result<Self, ClipboardError> {
        let started = Instant::now();
        loop {
            // SAFETY: no owner window; the clipboard is closed in Drop.
            if unsafe { OpenClipboard(HWND::default()) }.is_ok() {
                return Ok(Self);
            }
            if started.elapsed() >= OPEN_WAIT {
                return Err(ClipboardError::Busy);
            }
            std::thread::sleep(Duration::from_millis(10));
        }
    }
}

impl Drop for Open {
    fn drop(&mut self) {
        // SAFETY: this value opened the clipboard.
        let _ = unsafe { CloseClipboard() };
    }
}

/// The text on the clipboard as UTF-8, or None when it holds no text.
pub fn read() -> Result<Option<Vec<u8>>, ClipboardError> {
    let _open = Open::acquire()?;
    // SAFETY: the clipboard is open; the format is a constant.
    if unsafe { IsClipboardFormatAvailable(CF_UNICODETEXT) }.is_err() {
        return Ok(None);
    }
    // SAFETY: the clipboard is open; the handle stays the clipboard's.
    let Ok(handle) = (unsafe { GetClipboardData(CF_UNICODETEXT) }) else {
        return Ok(None);
    };
    let memory = HGLOBAL(handle.0);
    // SAFETY: a CF_UNICODETEXT handle is global memory; it is unlocked below.
    let pointer = unsafe { GlobalLock(memory) }.cast::<u16>();
    if pointer.is_null() {
        return Ok(None);
    }
    // SAFETY: the same handle; its size in bytes.
    let units = unsafe { GlobalSize(memory) } / 2;
    // SAFETY: `units` u16 values are readable at `pointer` while locked.
    let slice = unsafe { std::slice::from_raw_parts(pointer, units) };
    let end = slice.iter().position(|&unit| unit == 0).unwrap_or(units);
    let text = String::from_utf16_lossy(&slice[..end]);
    // SAFETY: locked above.
    let _ = unsafe { GlobalUnlock(memory) };
    Ok(Some(text.into_bytes()))
}

/// Put `data` (UTF-8 text) on the clipboard, replacing what was there.
pub fn write(mime: &str, data: &[u8]) -> Result<(), ClipboardError> {
    if !mime.to_ascii_lowercase().starts_with("text/") {
        return Err(ClipboardError::NotText(mime.to_owned()));
    }
    if data.len() > MAX_BYTES {
        return Err(ClipboardError::TooLarge);
    }
    let text = std::str::from_utf8(data).map_err(|_| ClipboardError::InvalidUtf8)?;
    let mut units: Vec<u16> = text.encode_utf16().collect();
    units.push(0);
    let bytes = units.len() * 2;
    // SAFETY: a movable allocation, freed below unless the clipboard takes it.
    let memory = unsafe { GlobalAlloc(GMEM_MOVEABLE, bytes) }
        .map_err(|error| ClipboardError::Failed(error.to_string()))?;
    // SAFETY: the allocation above, `bytes` long.
    let pointer = unsafe { GlobalLock(memory) }.cast::<u16>();
    if pointer.is_null() {
        // SAFETY: allocated above and not given away.
        let _ = unsafe { GlobalFree(memory) };
        return Err(ClipboardError::Failed("GlobalLock failed".to_owned()));
    }
    // SAFETY: `units.len()` u16 values fit in the `bytes`-long allocation.
    unsafe { std::ptr::copy_nonoverlapping(units.as_ptr(), pointer, units.len()) };
    // SAFETY: locked above.
    let _ = unsafe { GlobalUnlock(memory) };
    let stored = Open::acquire().and_then(|_open| {
        // SAFETY: the clipboard is open.
        unsafe { EmptyClipboard() }.map_err(|error| ClipboardError::Failed(error.to_string()))?;
        // SAFETY: on success the system owns `memory`; on failure it stays ours.
        unsafe { SetClipboardData(CF_UNICODETEXT, HANDLE(memory.0)) }
            .map(|_| ())
            .map_err(|error| ClipboardError::Failed(error.to_string()))
    });
    if stored.is_err() {
        // SAFETY: the clipboard did not take the allocation.
        let _ = unsafe { GlobalFree(memory) };
    }
    stored
}

/// Empty the clipboard.
pub fn clear() -> Result<(), ClipboardError> {
    let _open = Open::acquire()?;
    // SAFETY: the clipboard is open.
    unsafe { EmptyClipboard() }.map_err(|error| ClipboardError::Failed(error.to_string()))
}
