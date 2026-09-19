//! Clipboard access through the wl-clipboard programs (Task 5.4).
//!
//! The same `wl-paste` and `wl-copy` invocations the Python input path has
//! always made, with the same arguments, so one fixture
//! (`tests/fixtures/native/clipboard_cases.json`) pins both sides. GNOME does
//! not offer `wlr-data-control`, and no library speaking another protocol has
//! been verified on this machine, so the programs stay.
//!
//! Three rules carried over from the Python side:
//!
//! * `wl-copy` keeps running in the background as the clipboard's owner. Its
//!   stdout and stderr go to `/dev/null`: a pipe would be held open by that
//!   background process, and anything waiting for EOF waits until its timeout.
//!   Measured on the Python side, a 10 s timeout locked the keyboard tool.
//! * Only the first offered MIME type is saved. Other representations are
//!   lost on restore, and the capability report says so.
//! * Content is read with `--no-newline`, for every type: `wl-paste` appends
//!   a newline to anything it counts as text (`UTF8_STRING` too, which
//!   `wl-copy` lists first after a restore), and the restore would no longer
//!   be byte-exact.
//!
//! Clipboard bytes never appear in a log line or an error message.

use std::ffi::OsString;
use std::io::{self, Read, Write};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use pcbridge_core::MAX_BINARY_BYTES;

/// The type typed text is offered as, as on the Python side.
pub const TEXT_MIME: &str = "text/plain;charset=utf-8";

/// Per program run, as on the Python side.
pub const PROGRAM_TIMEOUT: Duration = Duration::from_secs(10);

/// Largest content one IPC response can carry.
pub const MAX_CONTENT_BYTES: usize = MAX_BINARY_BYTES as usize;

const MAX_LISTING_BYTES: usize = 64 * 1024;
const MAX_MIME_BYTES: usize = 256;
const POLL: Duration = Duration::from_millis(5);

/// One saved representation of the clipboard.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Saved {
    pub mime: String,
    pub data: Vec<u8>,
}

#[derive(Debug, thiserror::Error)]
pub enum ClipboardError {
    #[error("{program} was not found; install wl-clipboard")]
    Missing { program: &'static str },
    #[error("{program} did not finish within {} ms", timeout.as_millis())]
    Timeout {
        program: &'static str,
        timeout: Duration,
    },
    #[error("{program} failed")]
    Failed { program: &'static str },
    #[error("the clipboard holds more than {limit} bytes")]
    TooLarge { limit: usize },
    #[error("the MIME type must be 1 to {MAX_MIME_BYTES} bytes without control characters")]
    InvalidMime,
    #[error("{program} could not run: {source}")]
    Io {
        program: &'static str,
        #[source]
        source: io::Error,
    },
}

/// What a `wl-paste` run printed, and whether it succeeded.
#[derive(Debug)]
pub struct Pasted {
    pub success: bool,
    pub stdout: Vec<u8>,
}

/// Runs the two programs. Production spawns them; tests model a clipboard.
pub trait Programs {
    /// `wl-paste <args>`: stdout read up to `limit` bytes.
    ///
    /// # Errors
    /// The program is missing, times out, or prints more than `limit` bytes.
    fn paste(&self, args: &[&str], limit: usize) -> Result<Pasted, ClipboardError>;

    /// `wl-copy <args>` with `stdin`, stdout and stderr on `/dev/null`.
    /// Returns whether it exited successfully.
    ///
    /// # Errors
    /// The program is missing or times out.
    fn copy(&self, args: &[&str], stdin: Option<&[u8]>) -> Result<bool, ClipboardError>;
}

/// The three clipboard operations text input needs.
#[derive(Debug)]
pub struct Clipboard<P> {
    programs: P,
    max_content: usize,
}

impl<P: Programs> Clipboard<P> {
    pub const fn new(programs: P) -> Self {
        Self::with_limit(programs, MAX_CONTENT_BYTES)
    }

    pub const fn with_limit(programs: P, max_content: usize) -> Self {
        Self {
            programs,
            max_content,
        }
    }

    /// The first offered type and its bytes. `None` when the clipboard is
    /// empty or cannot be read, which the caller restores as a clear.
    ///
    /// # Errors
    /// A program is missing or times out, or the content exceeds the limit.
    pub fn save(&self) -> Result<Option<Saved>, ClipboardError> {
        let listing = self.programs.paste(&["--list-types"], MAX_LISTING_BYTES)?;
        if !listing.success || listing.stdout.iter().all(u8::is_ascii_whitespace) {
            return Ok(None);
        }
        let text = String::from_utf8_lossy(&listing.stdout);
        let mime = text.lines().next().unwrap_or_default().trim().to_owned();
        // Always `--no-newline`: wl-paste appends a newline to every type it
        // counts as text, not only `text/*`, and after one restore wl-copy
        // lists UTF8_STRING, STRING and TEXT first (measured 2026-09-19).
        let content = self
            .programs
            .paste(&["--type", mime.as_str(), "--no-newline"], self.max_content)?;
        if !content.success {
            return Ok(None);
        }
        Ok(Some(Saved {
            mime,
            data: content.stdout,
        }))
    }

    /// Offer `data` as `mime`.
    ///
    /// # Errors
    /// The MIME type is invalid, or `wl-copy` is missing, times out or fails.
    pub fn put(&self, mime: &str, data: &[u8]) -> Result<(), ClipboardError> {
        validate_mime(mime)?;
        if self.programs.copy(&["--type", mime], Some(data))? {
            Ok(())
        } else {
            Err(ClipboardError::Failed { program: "wl-copy" })
        }
    }

    /// Empty the clipboard.
    ///
    /// # Errors
    /// `wl-copy` is missing, times out or fails.
    pub fn clear(&self) -> Result<(), ClipboardError> {
        if self.programs.copy(&["--clear"], None)? {
            Ok(())
        } else {
            Err(ClipboardError::Failed { program: "wl-copy" })
        }
    }
}

/// A MIME type that can be passed as one program argument.
///
/// The argument never reaches a shell, so a space is harmless and some
/// applications offer `text/plain; charset=utf-8`. What is refused is what
/// could not have come from `wl-paste --list-types` in one line.
///
/// # Errors
/// Empty, longer than 256 bytes, or containing a control character.
pub fn validate_mime(mime: &str) -> Result<(), ClipboardError> {
    let clean = !mime.chars().any(char::is_control);
    if mime.is_empty() || mime.len() > MAX_MIME_BYTES || !clean {
        return Err(ClipboardError::InvalidMime);
    }
    Ok(())
}

/// The real programs, found on `PATH` unless given as paths.
#[derive(Debug)]
pub struct SystemPrograms {
    paste: OsString,
    copy: OsString,
    timeout: Duration,
}

impl Default for SystemPrograms {
    fn default() -> Self {
        Self::with_programs("wl-paste", "wl-copy", PROGRAM_TIMEOUT)
    }
}

impl SystemPrograms {
    pub fn with_programs(
        paste: impl Into<OsString>,
        copy: impl Into<OsString>,
        timeout: Duration,
    ) -> Self {
        Self {
            paste: paste.into(),
            copy: copy.into(),
            timeout,
        }
    }
}

impl Programs for SystemPrograms {
    fn paste(&self, args: &[&str], limit: usize) -> Result<Pasted, ClipboardError> {
        const PROGRAM: &str = "wl-paste";
        let mut child = Command::new(&self.paste)
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|source| spawn_error(PROGRAM, source))?;
        let stdout = child.stdout.take().expect("stdout is piped");
        // Read one byte past the limit, then drop the pipe: a program still
        // writing gets EPIPE and exits instead of blocking until the timeout.
        let reader = thread::spawn(move || {
            let mut data = Vec::new();
            stdout
                .take(u64::try_from(limit).unwrap_or(u64::MAX).saturating_add(1))
                .read_to_end(&mut data)
                .map(|_| data)
        });
        let status = wait_until(&mut child, self.timeout, PROGRAM);
        let data = reader.join().expect("the reader thread does not panic");
        let status = status?;
        let data = data.map_err(|source| ClipboardError::Io {
            program: PROGRAM,
            source,
        })?;
        if data.len() > limit {
            return Err(ClipboardError::TooLarge { limit });
        }
        Ok(Pasted {
            success: status.success(),
            stdout: data,
        })
    }

    fn copy(&self, args: &[&str], stdin: Option<&[u8]>) -> Result<bool, ClipboardError> {
        const PROGRAM: &str = "wl-copy";
        let mut child = Command::new(&self.copy)
            .args(args)
            .stdin(if stdin.is_some() {
                Stdio::piped()
            } else {
                Stdio::null()
            })
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|source| spawn_error(PROGRAM, source))?;
        let status = match (stdin, child.stdin.take()) {
            (Some(data), Some(mut pipe)) => thread::scope(|scope| {
                // A writer thread, so a program that stops reading cannot
                // block past the deadline; killing it ends the write too.
                let writer = scope.spawn(move || pipe.write_all(data));
                let status = wait_until(&mut child, self.timeout, PROGRAM);
                let written = writer.join().expect("the writer thread does not panic");
                let status = status?;
                // After a clean exit every byte must have gone in, or the
                // clipboard holds a truncated copy. A failed exit is
                // reported by the status instead.
                if status.success() {
                    written.map_err(|source| ClipboardError::Io {
                        program: PROGRAM,
                        source,
                    })?;
                }
                Ok(status)
            }),
            _ => wait_until(&mut child, self.timeout, PROGRAM),
        }?;
        Ok(status.success())
    }
}

fn spawn_error(program: &'static str, source: io::Error) -> ClipboardError {
    if source.kind() == io::ErrorKind::NotFound {
        ClipboardError::Missing { program }
    } else {
        ClipboardError::Io { program, source }
    }
}

fn wait_until(
    child: &mut Child,
    timeout: Duration,
    program: &'static str,
) -> Result<ExitStatus, ClipboardError> {
    let deadline = Instant::now() + timeout;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return Ok(status),
            Ok(None) if Instant::now() < deadline => thread::sleep(POLL),
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(ClipboardError::Timeout { program, timeout });
            }
            Err(source) => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(ClipboardError::Io { program, source });
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_mime_type_is_one_line_of_bounded_length() {
        for good in [
            TEXT_MIME,
            "image/png",
            "text/plain; charset=utf-8",
            "application/x-kde-cutselection",
        ] {
            assert!(validate_mime(good).is_ok(), "{good}");
        }
        let long = "a".repeat(257);
        for bad in ["", "text/plain\n", "--clear\0", "a\tb", long.as_str()] {
            assert!(validate_mime(bad).is_err(), "{bad:?}");
        }
    }
}
