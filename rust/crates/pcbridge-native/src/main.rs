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
    }
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
        #[cfg(feature = "test-harness")]
        [Some("--test-mode")] => Ok(Command::Serve(BackendMode::deterministic_test())),
        _ => Err("unsupported command-line arguments"),
    }
}
