//! Could a capture start here -- answered without starting one.
//!
//! This backs the helper's `capabilities` request, which used to answer
//! `capture.monitor: supported` unconditionally: a build-time claim, true on a
//! machine where capture could never work. It now looks at the two things a
//! capture needs from the session, Mutter's ScreenCast service on the session
//! bus and a PipeWire socket, and at nothing more. No ScreenCast session, no
//! stream, no share indicator, no permission prompt. `display.snapshot` and
//! `capture.frame` still report their own failures when they run.

use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde_json::{Value, json};
use zbus::connection::Builder;
use zbus::fdo::DBusProxy;
use zbus::names::BusName;

pub const SCREENCAST_SERVICE: &str = "org.gnome.Mutter.ScreenCast";
const BUS_TIMEOUT: Duration = Duration::from_millis(500);

/// What the probe saw, kept apart from the verdict so every combination can
/// be tested without a session bus.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CaptureReadiness {
    /// `Ok(owned)` from the bus, or why the bus could not be asked.
    pub screencast: Result<bool, String>,
    /// The socket a PipeWire client would connect to.
    pub pipewire_socket: PathBuf,
    pub pipewire_socket_exists: bool,
}

#[must_use]
pub fn probe() -> CaptureReadiness {
    let pipewire_socket = pipewire_socket(|name| std::env::var_os(name));
    CaptureReadiness {
        screencast: screencast_owned(),
        pipewire_socket_exists: pipewire_socket.exists(),
        pipewire_socket,
    }
}

fn screencast_owned() -> Result<bool, String> {
    zbus::block_on(async {
        let connection = Builder::session()
            .map_err(|error| error.to_string())?
            .method_timeout(BUS_TIMEOUT)
            .build()
            .await
            .map_err(|error| error.to_string())?;
        let proxy = DBusProxy::new(&connection)
            .await
            .map_err(|error| error.to_string())?;
        let name = BusName::try_from(SCREENCAST_SERVICE).map_err(|error| error.to_string())?;
        proxy
            .name_has_owner(name)
            .await
            .map_err(|error| error.to_string())
    })
}

/// Where libpipewire would look: `PIPEWIRE_REMOTE` (a name, or an absolute
/// path) inside `PIPEWIRE_RUNTIME_DIR`, else `XDG_RUNTIME_DIR`.
pub fn pipewire_socket(var: impl Fn(&str) -> Option<OsString>) -> PathBuf {
    let remote = var("PIPEWIRE_REMOTE")
        .filter(|value| !value.is_empty())
        .map_or_else(|| PathBuf::from("pipewire-0"), PathBuf::from);
    if remote.is_absolute() {
        return remote;
    }
    var("PIPEWIRE_RUNTIME_DIR")
        .or_else(|| var("XDG_RUNTIME_DIR"))
        .filter(|value| !value.is_empty())
        .map_or_else(|| PathBuf::from("/run/user/unknown"), PathBuf::from)
        .join(remote)
}

/// The `capture.monitor` entry of a `capabilities` response.
#[must_use]
pub fn capture_monitor(readiness: &CaptureReadiness) -> Value {
    let unavailable = |code: &str, reason: String| {
        json!({
            "name": "capture.monitor",
            "status": "unavailable",
            "permission_scope": "os.capture",
            "reason_code": code,
            "reason": reason,
        })
    };
    match &readiness.screencast {
        Err(error) => {
            return unavailable(
                "BACKEND_UNAVAILABLE",
                format!("the session bus could not be asked: {error}"),
            );
        }
        Ok(false) => {
            return unavailable(
                "BACKEND_UNAVAILABLE",
                format!("{SCREENCAST_SERVICE} has no owner; this is not a Mutter session"),
            );
        }
        Ok(true) => {}
    }
    if !readiness.pipewire_socket_exists {
        return unavailable(
            "DEPENDENCY_MISSING",
            format!(
                "no PipeWire socket at {}",
                readiness.pipewire_socket.display()
            ),
        );
    }
    json!({
        "name": "capture.monitor",
        "status": "supported",
        "permission_scope": "os.capture",
    })
}

/// The `input.keyboard` entry of a `capabilities` response.
///
/// This intentionally checks only whether the node exists. Opening it here
/// would make a default, read-only capability request touch `/dev/uinput`;
/// access is therefore verified only by an explicit keyboard request.
#[must_use]
pub fn input_keyboard() -> Value {
    if Path::new("/dev/uinput").exists() {
        json!({
            "name": "input.keyboard",
            "status": "degraded",
            "permission_scope": "os.keyboard",
            "reason": "device access is checked on the first explicit keyboard request",
        })
    } else {
        json!({
            "name": "input.keyboard",
            "status": "unavailable",
            "permission_scope": "os.keyboard",
            "reason_code": "DEPENDENCY_MISSING",
            "reason": "the /dev/uinput device is missing",
        })
    }
}
