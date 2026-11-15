// Linux needs no unsafe code at all. Windows reaches the Win32 API, which
// is unsafe to call, so there `unsafe` is denied everywhere except the
// Windows platform module, which allows it explicitly.
#![cfg_attr(not(windows), forbid(unsafe_code))]
#![cfg_attr(windows, deny(unsafe_code))]

pub mod accessibility;
pub mod build_info;
// Each system has a dispatcher of its own with the same protocol (see
// docs/native/protocol-v1.md): the Linux one is built around AT-SPI,
// PipeWire, uinput and X11; the Windows one around Win32.
#[cfg_attr(windows, path = "dispatch_windows.rs")]
pub mod dispatch;
pub mod fail_closed;
pub mod glow;
pub mod input;
#[cfg(target_os = "linux")]
pub mod lifecycle;
pub mod platform;

use std::io::{Read, Write};

use dispatch::{CloseConnection, Dispatcher};
use pcbridge_core::{ProtocolError, read_frame, write_frame};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ExitReason {
    Eof,
    Shutdown,
    IncompatibleProtocol,
}

pub fn run<R: Read, W: Write>(
    mut reader: R,
    mut writer: W,
    mut dispatcher: Dispatcher,
) -> Result<ExitReason, ProtocolError> {
    loop {
        let Some(frame) = read_frame(&mut reader)? else {
            return Ok(ExitReason::Eof);
        };
        let outcome = dispatcher.dispatch(frame)?;
        write_frame(&mut writer, &outcome.response, &outcome.binary)?;
        match outcome.close {
            None => {}
            Some(CloseConnection::Shutdown) => return Ok(ExitReason::Shutdown),
            Some(CloseConnection::IncompatibleProtocol) => {
                return Ok(ExitReason::IncompatibleProtocol);
            }
        }
    }
}
