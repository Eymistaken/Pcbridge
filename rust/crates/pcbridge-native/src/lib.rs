#![forbid(unsafe_code)]

pub mod dispatch;
pub mod lifecycle;

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
        write_frame(&mut writer, &outcome.response, &[])?;
        match outcome.close {
            None => {}
            Some(CloseConnection::Shutdown) => return Ok(ExitReason::Shutdown),
            Some(CloseConnection::IncompatibleProtocol) => {
                return Ok(ExitReason::IncompatibleProtocol);
            }
        }
    }
}
