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
    name_owned(SCREENCAST_SERVICE)
}

/// Is `service` owned on the session bus? A question to the bus daemon only;
/// the service itself is not called.
fn name_owned(service: &str) -> Result<bool, String> {
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
        let name = BusName::try_from(service).map_err(|error| error.to_string())?;
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

/// The `input.pointer` entry of a `capabilities` response.
///
/// As with the keyboard probe, this never opens `/dev/uinput`; explicit input
/// requests are the only operations allowed to create a virtual device.
#[must_use]
pub fn input_pointer() -> Value {
    if Path::new("/dev/uinput").exists() {
        json!({
            "name": "input.pointer",
            "status": "degraded",
            "permission_scope": "os.pointer",
            "reason": "device access is checked on the first explicit pointer request",
        })
    } else {
        json!({
            "name": "input.pointer",
            "status": "unavailable",
            "permission_scope": "os.pointer",
            "reason_code": "DEPENDENCY_MISSING",
            "reason": "the /dev/uinput device is missing",
        })
    }
}

/// The SECOND, relative device. Same node, same permission scope: a separate
/// device but not a separate door.
pub fn input_pointer_relative() -> Value {
    if Path::new("/dev/uinput").exists() {
        json!({
            "name": "input.pointer_relative",
            "status": "degraded",
            "permission_scope": "os.pointer",
            "reason": "device access is checked on the first explicit pointer request",
        })
    } else {
        json!({
            "name": "input.pointer_relative",
            "status": "unavailable",
            "permission_scope": "os.pointer",
            "reason_code": "DEPENDENCY_MISSING",
            "reason": "the /dev/uinput device is missing",
        })
    }
}

/// Shown with a usable clipboard: the restore keeps one representation.
pub const SINGLE_MIME_LIMITATION: &str =
    "Clipboard restore keeps only the first offered type; other representations are lost.";

/// A `clipboard.read` or `clipboard.write` entry of a `capabilities` response.
///
/// Nothing is run: a capability request must not touch the user's clipboard.
/// It checks what the Python side checks, the program on `PATH` and the
/// session's Wayland socket.
#[must_use]
pub fn clipboard(name: &str, program: &str) -> Value {
    if !on_path(program) {
        return json!({
            "name": name,
            "status": "unavailable",
            "permission_scope": "os.clipboard",
            "reason_code": "DEPENDENCY_MISSING",
            "reason": format!("{program} is not on PATH; install wl-clipboard"),
        });
    }
    if wayland_socket().is_none() {
        return json!({
            "name": name,
            "status": "unavailable",
            "permission_scope": "os.clipboard",
            "reason_code": "BACKEND_UNAVAILABLE",
            "reason": "this session has no Wayland socket",
        });
    }
    // The restore is the write, and it is the restore that loses the other
    // representations; the Python provider reports it the same way.
    let limitations: &[&str] = if name == "clipboard.write" {
        &[SINGLE_MIME_LIMITATION]
    } else {
        &[]
    };
    json!({
        "name": name,
        "status": "supported",
        "permission_scope": "os.clipboard",
        "limitations": limitations,
    })
}

fn on_path(program: &str) -> bool {
    use std::os::unix::fs::PermissionsExt;

    std::env::var_os("PATH").is_some_and(|path| {
        std::env::split_paths(&path).any(|directory| {
            std::fs::metadata(directory.join(program))
                .is_ok_and(|meta| meta.is_file() && meta.permissions().mode() & 0o111 != 0)
        })
    })
}

/// `$WAYLAND_DISPLAY`, relative to `$XDG_RUNTIME_DIR` unless absolute.
fn wayland_socket() -> Option<PathBuf> {
    let display = PathBuf::from(std::env::var_os("WAYLAND_DISPLAY")?);
    if display.as_os_str().is_empty() {
        return None;
    }
    let path = if display.is_absolute() {
        display
    } else {
        PathBuf::from(std::env::var_os("XDG_RUNTIME_DIR")?).join(display)
    };
    path.exists().then_some(path)
}

/// The session's accessibility bus launcher.
pub const ACCESSIBILITY_BUS: &str = "org.a11y.Bus";

/// Shown with a usable window list: only what publishes a tree is seen.
pub const WINDOW_LIST_LIMITATION: &str = "Only accessibility-visible applications are listed.";

/// Can the accessibility bus be found? Asked of the bus daemon only: no
/// application is read and no accessibility connection is opened.
pub fn accessibility_bus_owned() -> Result<bool, String> {
    name_owned(ACCESSIBILITY_BUS)
}

/// The `accessibility.read`, `window.list` and `accessibility.action`
/// entries of a `capabilities` response. The Python provider reports the
/// window list as degraded for the same reason: an application that
/// publishes no tree has no window here.
#[must_use]
pub fn accessibility(owned: &Result<bool, String>) -> [Value; 3] {
    let reason = match owned {
        Ok(true) => None,
        Ok(false) => Some(format!(
            "{ACCESSIBILITY_BUS} has no owner on the session bus"
        )),
        Err(error) => Some(format!("the session bus could not be asked: {error}")),
    };
    match reason {
        None => [
            json!({
                "name": "accessibility.read",
                "status": "supported",
                "permission_scope": "os.accessibility",
            }),
            json!({
                "name": "window.list",
                "status": "degraded",
                "permission_scope": "os.accessibility",
                "limitations": [WINDOW_LIST_LIMITATION],
            }),
            json!({
                "name": "accessibility.action",
                "status": "supported",
                "permission_scope": "os.accessibility",
            }),
        ],
        Some(reason) => ["accessibility.read", "window.list", "accessibility.action"].map(|name| {
            json!({
                "name": name,
                "status": "unavailable",
                "permission_scope": "os.accessibility",
                "reason_code": "BACKEND_UNAVAILABLE",
                "reason": reason,
            })
        }),
    }
}
