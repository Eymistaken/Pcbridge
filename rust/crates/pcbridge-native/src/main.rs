#![forbid(unsafe_code)]

use std::ffi::OsString;
use std::io;
use std::process::ExitCode;

use pcbridge_native::build_info;
use pcbridge_native::dispatch::{BackendMode, Dispatcher};

enum Command {
    Serve(BackendMode),
    Version,
    BuildInfo,
    IdleWatch,
    GlowWatch(std::path::PathBuf, pcbridge_core::LeaseToken),
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
        Command::IdleWatch => idle_watch(),
        Command::GlowWatch(directory, token) => {
            match pcbridge_native::platform::linux::glow_watch::watch(&directory, token) {
                Ok(()) => ExitCode::SUCCESS,
                Err(error) => {
                    eprintln!("pcbridge-native glow-watch: {error}");
                    ExitCode::from(1)
                }
            }
        }
    }
}

/// Wayland input idle time (see `platform::linux::idle`); runs until the
/// compositor goes away, and exits non-zero so the daemon restarts it.
fn idle_watch() -> ExitCode {
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
        [Some("idle-watch")] => Ok(Command::IdleWatch),
        [Some("glow-watch"), Some(_), Some(grant_id), Some(epoch)] if !grant_id.is_empty() => {
            Ok(Command::GlowWatch(
                std::path::PathBuf::from(&arguments[1]),
                pcbridge_core::LeaseToken::new(
                    *grant_id,
                    epoch.parse().map_err(|_| "invalid revoke epoch")?,
                ),
            ))
        }
        #[cfg(feature = "test-harness")]
        [Some("--test-mode")] => Ok(Command::Serve(BackendMode::deterministic_test())),
        _ => Err("unsupported command-line arguments"),
    }
}
