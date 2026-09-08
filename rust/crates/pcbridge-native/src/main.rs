#![forbid(unsafe_code)]

use std::io;
use std::process::ExitCode;

use pcbridge_native::dispatch::{BackendMode, Dispatcher};

fn main() -> ExitCode {
    let mode = match mode_from_args() {
        Ok(mode) => mode,
        Err(message) => {
            eprintln!("pcbridge-native: {message}");
            return ExitCode::from(2);
        }
    };

    let stdin = io::stdin();
    let stdout = io::stdout();
    match pcbridge_native::run(stdin.lock(), stdout.lock(), Dispatcher::new(mode)) {
        Ok(_) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("pcbridge-native protocol error: {error}");
            ExitCode::from(2)
        }
    }
}

fn mode_from_args() -> Result<BackendMode, &'static str> {
    let mut arguments = std::env::args_os();
    let _program = arguments.next();
    let first = arguments.next();
    let has_more = arguments.next().is_some();

    if first.is_none() && !has_more {
        return Ok(BackendMode::production());
    }

    #[cfg(feature = "test-harness")]
    if first.as_deref() == Some(std::ffi::OsStr::new("--test-mode")) && !has_more {
        return Ok(BackendMode::deterministic_test());
    }

    Err("unsupported command-line arguments")
}
