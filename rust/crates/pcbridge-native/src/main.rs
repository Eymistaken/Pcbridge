#![cfg_attr(not(windows), forbid(unsafe_code))]
#![cfg_attr(windows, deny(unsafe_code))]

use std::ffi::OsString;
use std::io;
use std::process::ExitCode;

use pcbridge_native::build_info;
use pcbridge_native::dispatch::{BackendMode, Dispatcher};

enum Command {
    Serve(BackendMode),
    Version,
    BuildInfo,
    IdleWatch(Option<std::path::PathBuf>),
    GlowWatch(
        std::path::PathBuf,
        pcbridge_core::LeaseToken,
        Option<std::path::PathBuf>,
    ),
}

fn main() -> ExitCode {
    let command = match command_from_args(std::env::args_os().skip(1).collect()) {
        Ok(command) => command,
        Err(message) => {
            eprintln!("pcbridge-native: {message}");
            return ExitCode::from(2);
        }
    };

    match command {
        // Neither of these opens the session bus, PipeWire or a state
        // directory: `doctor.sh` uses them to describe an install without
        // asking for anything.
        Command::Version => {
            println!("{}", build_info::version_line());
            ExitCode::SUCCESS
        }
        Command::BuildInfo => {
            println!("{}", build_info::build_info());
            ExitCode::SUCCESS
        }
        Command::Serve(mode) => serve(mode),
        Command::IdleWatch(path) => idle_watch(path),
        Command::GlowWatch(directory, token, lock_program) => {
            glow_watch(&directory, token, lock_program)
        }
    }
}

#[cfg(target_os = "linux")]
fn glow_watch(
    directory: &std::path::Path,
    token: pcbridge_core::LeaseToken,
    _lock_program: Option<std::path::PathBuf>,
) -> ExitCode {
    match pcbridge_native::platform::linux::glow_watch::watch(directory, token) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("pcbridge-native glow-watch: {error}");
            ExitCode::from(1)
        }
    }
}

/// The grant frame and the tray icon (see `platform::windows::frame`).
#[cfg(windows)]
fn glow_watch(
    directory: &std::path::Path,
    token: pcbridge_core::LeaseToken,
    lock_program: Option<std::path::PathBuf>,
) -> ExitCode {
    match pcbridge_native::platform::windows::frame::watch(directory, token, lock_program) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("pcbridge-native glow-watch: {error}");
            ExitCode::from(1)
        }
    }
}

/// The user's own input apart from pcbridge's (see
/// `platform::windows::idle`); runs until ended.
#[cfg(windows)]
fn idle_watch(path: Option<std::path::PathBuf>) -> ExitCode {
    let Some(path) = path.or_else(pcbridge_native::platform::windows::idle::default_path) else {
        eprintln!("pcbridge-native idle-watch: LOCALAPPDATA is not set");
        return ExitCode::from(2);
    };
    let reason = pcbridge_native::platform::windows::idle::watch(&path);
    eprintln!("pcbridge-native idle-watch: {reason}");
    ExitCode::from(1)
}

/// Wayland input idle time (see `platform::linux::idle`); runs until the
/// compositor goes away, and exits non-zero so the daemon restarts it.
#[cfg(target_os = "linux")]
fn idle_watch(_path: Option<std::path::PathBuf>) -> ExitCode {
    let Some(path) = pcbridge_native::platform::linux::idle::state_path() else {
        eprintln!("pcbridge-native idle-watch: XDG_RUNTIME_DIR is not set");
        return ExitCode::from(2);
    };
    let reason = pcbridge_native::platform::linux::idle::watch(&path);
    eprintln!("pcbridge-native idle-watch: {reason}");
    ExitCode::from(1)
}

fn serve(mode: BackendMode) -> ExitCode {
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

fn command_from_args(arguments: Vec<OsString>) -> Result<Command, &'static str> {
    let flags: Vec<Option<&str>> = arguments.iter().map(|argument| argument.to_str()).collect();
    match flags.as_slice() {
        [] => Ok(Command::Serve(BackendMode::production())),
        [Some("--version")] => Ok(Command::Version),
        [Some("--build-info")] => Ok(Command::BuildInfo),
        [Some("idle-watch")] => Ok(Command::IdleWatch(None)),
        // Windows: where to write the record (Linux takes it from
        // XDG_RUNTIME_DIR).
        #[cfg(windows)]
        [Some("idle-watch"), Some(_)] => Ok(Command::IdleWatch(Some(std::path::PathBuf::from(
            &arguments[1],
        )))),
        [Some("glow-watch"), Some(_), Some(grant_id), Some(epoch)] if !grant_id.is_empty() => {
            Ok(Command::GlowWatch(
                std::path::PathBuf::from(&arguments[1]),
                pcbridge_core::LeaseToken::new(
                    *grant_id,
                    epoch.parse().map_err(|_| "invalid revoke epoch")?,
                ),
                None,
            ))
        }
        // Windows: the tray icon's "Lock now" runs `<program> lock`.
        #[cfg(windows)]
        [
            Some("glow-watch"),
            Some(_),
            Some(grant_id),
            Some(epoch),
            Some(_),
        ] if !grant_id.is_empty() => Ok(Command::GlowWatch(
            std::path::PathBuf::from(&arguments[1]),
            pcbridge_core::LeaseToken::new(
                *grant_id,
                epoch.parse().map_err(|_| "invalid revoke epoch")?,
            ),
            Some(std::path::PathBuf::from(&arguments[4])),
        )),
        #[cfg(feature = "test-harness")]
        [Some("--test-mode")] => Ok(Command::Serve(BackendMode::deterministic_test())),
        _ => Err("unsupported command-line arguments"),
    }
}
