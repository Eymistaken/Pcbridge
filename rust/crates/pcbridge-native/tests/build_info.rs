//! What the binary says about itself, asked the way `doctor.sh` and the
//! packaging smoke test ask: a separate process, no protocol, no session.

use std::process::{Command, Output};

use pcbridge_native::platform::linux::readiness::{
    CaptureReadiness, SCREENCAST_SERVICE, capture_monitor, pipewire_socket,
};
use serde_json::Value;

fn run(arguments: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_pcbridge-native"))
        .args(arguments)
        // Neither flag may need the desktop session.
        .env_remove("DBUS_SESSION_BUS_ADDRESS")
        .env_remove("XDG_RUNTIME_DIR")
        .output()
        .expect("helper should start")
}

#[test]
fn build_info_is_one_json_object() {
    let output = run(&["--build-info"]);
    assert!(output.status.success(), "{output:?}");
    assert!(output.stderr.is_empty(), "{output:?}");

    let info: Value = serde_json::from_slice(&output.stdout).expect("build info JSON");
    assert_eq!(info["name"], "pcbridge-native");
    assert_eq!(info["version"], env!("CARGO_PKG_VERSION"));
    assert_eq!(info["protocol"]["major"], 1);
    assert_eq!(info["protocol"]["minor"], 0);
    assert!(
        info["build_id"].as_str().is_some_and(|id| !id.is_empty()),
        "{info}"
    );
    let target = info["target"].as_str().expect("target triple");
    assert!(target.starts_with(std::env::consts::ARCH), "{target}");
    assert!(target.contains(std::env::consts::OS), "{target}");
    assert!(
        matches!(info["profile"].as_str(), Some("debug" | "release")),
        "{info}"
    );
    assert_eq!(info["test_harness"], cfg!(feature = "test-harness"));
}

#[test]
fn version_is_one_human_line() {
    let output = run(&["--version"]);
    assert!(output.status.success(), "{output:?}");
    let line = String::from_utf8(output.stdout).expect("utf-8");
    assert!(
        line.starts_with(concat!("pcbridge-native ", env!("CARGO_PKG_VERSION"))),
        "{line}"
    );
    assert!(line.contains("protocol 1.0"), "{line}");
    assert_eq!(line.lines().count(), 1);
}

#[test]
fn anything_else_on_the_command_line_is_refused() {
    for arguments in [
        &["--build-info", "--version"][..],
        &["--versions"][..],
        &["serve"][..],
    ] {
        let output = run(arguments);
        assert_eq!(output.status.code(), Some(2), "{arguments:?}");
        assert!(output.stdout.is_empty(), "{arguments:?}");
    }
}

fn readiness(screencast: Result<bool, String>, socket_exists: bool) -> CaptureReadiness {
    CaptureReadiness {
        screencast,
        pipewire_socket: "/run/user/1000/pipewire-0".into(),
        pipewire_socket_exists: socket_exists,
    }
}

#[test]
fn capture_is_supported_only_when_mutter_and_pipewire_are_both_there() {
    let supported = capture_monitor(&readiness(Ok(true), true));
    assert_eq!(supported["status"], "supported");
    assert_eq!(supported["name"], "capture.monitor");
    assert!(supported.get("reason").is_none());

    let no_mutter = capture_monitor(&readiness(Ok(false), true));
    assert_eq!(no_mutter["status"], "unavailable");
    assert_eq!(no_mutter["reason_code"], "BACKEND_UNAVAILABLE");
    assert!(
        no_mutter["reason"]
            .as_str()
            .is_some_and(|reason| reason.contains(SCREENCAST_SERVICE))
    );

    let no_bus = capture_monitor(&readiness(Err("no address".to_owned()), true));
    assert_eq!(no_bus["status"], "unavailable");
    assert_eq!(no_bus["reason_code"], "BACKEND_UNAVAILABLE");

    let no_pipewire = capture_monitor(&readiness(Ok(true), false));
    assert_eq!(no_pipewire["status"], "unavailable");
    assert_eq!(no_pipewire["reason_code"], "DEPENDENCY_MISSING");
    assert!(
        no_pipewire["reason"]
            .as_str()
            .is_some_and(|reason| reason.contains("pipewire-0"))
    );
}

#[test]
fn the_pipewire_socket_is_found_where_libpipewire_looks() {
    let env = |pairs: &'static [(&'static str, &'static str)]| {
        move |name: &str| {
            pairs
                .iter()
                .find(|(key, _)| *key == name)
                .map(|(_, value)| (*value).into())
        }
    };
    assert_eq!(
        pipewire_socket(env(&[("XDG_RUNTIME_DIR", "/run/user/1000")])),
        std::path::PathBuf::from("/run/user/1000/pipewire-0")
    );
    assert_eq!(
        pipewire_socket(env(&[
            ("XDG_RUNTIME_DIR", "/run/user/1000"),
            ("PIPEWIRE_RUNTIME_DIR", "/custom"),
            ("PIPEWIRE_REMOTE", "pipewire-1"),
        ])),
        std::path::PathBuf::from("/custom/pipewire-1")
    );
    assert_eq!(
        pipewire_socket(env(&[("PIPEWIRE_REMOTE", "/abs/socket")])),
        std::path::PathBuf::from("/abs/socket")
    );
}
