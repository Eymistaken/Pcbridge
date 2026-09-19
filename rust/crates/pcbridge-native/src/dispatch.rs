use std::path::{Path, PathBuf};
use std::sync::{Arc, Weak};
use std::time::Duration;

use pcbridge_core::{
    ErrorBody, Frame, PROTOCOL_MAJOR, PROTOCOL_MINOR, ProtocolError, RequestHeader, ResponseHeader,
};
use serde::Deserialize;
use serde::de::DeserializeOwned;
use serde_json::{Value, json};

#[cfg(feature = "test-harness")]
use crate::lifecycle::LeaseFailure;
use crate::lifecycle::{FailClosed, Lifecycle, LifecycleFailure};
use crate::platform::linux::accessibility::bus::AtspiBus;
#[cfg(feature = "test-harness")]
use crate::platform::linux::accessibility::fixture::FixtureTree;
use crate::platform::linux::accessibility::{self, AccessibilityError, DumpRequest, Tree};
use crate::platform::linux::capture::{CaptureError, NativeCapture, NativeCaptureError};
use crate::platform::linux::clipboard::{self, Clipboard, ClipboardError, SystemPrograms};
#[cfg(feature = "test-harness")]
use crate::platform::linux::desktop_state::DeterministicDesktopState;
use crate::platform::linux::display::DisplayReader;
use crate::platform::linux::display::DisplaySnapshot;
use crate::platform::linux::input::{
    DEVICE_SETTLE, EvdevKeyboardDevice, EvdevPointerDevice, KeyboardError, KeyboardService,
    NativeKeyboard, NativePointer, PointerConfig, PointerError, PointerGeometry, PointerService,
    SystemClock, SystemPointerClock,
};
#[cfg(feature = "test-harness")]
use crate::platform::linux::input::{
    Keyboard, KeyboardDevice, KeyboardEvent, Pointer, PointerDevice, PointerEvent,
};
use crate::platform::linux::readiness;
use crate::platform::linux::session::{OpenOutcome, SessionFailure};

#[derive(Debug)]
pub enum BackendMode {
    Production {
        instance_id: String,
    },
    #[cfg(feature = "test-harness")]
    DeterministicTest,
}

impl BackendMode {
    #[must_use]
    pub fn production() -> Self {
        Self::Production {
            instance_id: format!("native-{}", std::process::id()),
        }
    }

    #[cfg(feature = "test-harness")]
    #[must_use]
    pub const fn deterministic_test() -> Self {
        Self::DeterministicTest
    }

    fn instance_id(&self) -> &str {
        match self {
            Self::Production { instance_id } => instance_id,
            #[cfg(feature = "test-harness")]
            Self::DeterministicTest => "test-native-instance",
        }
    }

    fn platform(&self) -> &str {
        match self {
            Self::Production { .. } => std::env::consts::OS,
            #[cfg(feature = "test-harness")]
            Self::DeterministicTest => "test",
        }
    }

    fn features(&self) -> Vec<&'static str> {
        match self {
            Self::Production { .. } => vec![
                "display.snapshot",
                "capture.on_demand",
                "capture.session_open",
                "input.keyboard",
                "input.pointer",
                "clipboard",
                "accessibility.read",
            ],
            #[cfg(feature = "test-harness")]
            Self::DeterministicTest => vec![
                "test.fixture",
                "test.lease",
                "input.keyboard",
                "input.pointer",
                "clipboard",
                "accessibility.read",
            ],
        }
    }

    fn capabilities(&self) -> Value {
        match self {
            // Probed on every request, and cheaply: a bus name owner and a
            // socket, never a session. This used to be a fixed `supported`,
            // true even where capture could not work.
            Self::Production { .. } => {
                let [read, windows] =
                    readiness::accessibility(&readiness::accessibility_bus_owned());
                json!({
                    "backend": "linux.mutter.pipewire",
                    "capabilities": [
                        readiness::capture_monitor(&readiness::probe()),
                        readiness::input_keyboard(),
                        readiness::input_pointer(),
                        readiness::clipboard("clipboard.read", "wl-paste"),
                        readiness::clipboard("clipboard.write", "wl-copy"),
                        read,
                        windows,
                    ],
                })
            }
            #[cfg(feature = "test-harness")]
            Self::DeterministicTest => json!({
                "backend": "test.fake",
                "capabilities": [
                    {
                        "name": "capture.monitor",
                        "status": "unavailable",
                        "reason": "deterministic protocol fixture",
                    }
                ],
            }),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CloseConnection {
    Shutdown,
    IncompatibleProtocol,
}

#[derive(Debug)]
pub struct DispatchOutcome {
    pub response: ResponseHeader,
    pub binary: Vec<u8>,
    pub close: Option<CloseConnection>,
}

struct KeyboardFailClosed(Weak<dyn KeyboardService>);

impl FailClosed for KeyboardFailClosed {
    fn close_fail_closed(&self, reason: LifecycleFailure) {
        if let Some(keyboard) = self.0.upgrade() {
            keyboard.close_fail_closed(reason);
        }
    }
}

struct PointerFailClosed(Weak<dyn PointerService>);

impl FailClosed for PointerFailClosed {
    fn close_fail_closed(&self, reason: LifecycleFailure) {
        if let Some(pointer) = self.0.upgrade() {
            pointer.close_fail_closed(reason);
        }
    }
}

#[cfg(feature = "test-harness")]
#[derive(Debug)]
struct NullKeyboardDevice;

#[cfg(feature = "test-harness")]
impl KeyboardDevice for NullKeyboardDevice {
    fn emit(&mut self, _events: &[KeyboardEvent]) -> std::io::Result<()> {
        Ok(())
    }
}

#[cfg(feature = "test-harness")]
#[derive(Debug)]
struct NullPointerDevice;

#[cfg(feature = "test-harness")]
impl PointerDevice for NullPointerDevice {
    fn emit(&mut self, _events: &[PointerEvent]) -> std::io::Result<()> {
        Ok(())
    }
}

#[derive(Clone, Copy)]
enum KeyAction {
    Key,
    Down,
    Up,
}

enum KeyboardSetupError {
    HoldChanged,
    Device(std::io::Error),
}

enum PointerRequestError {
    Lifecycle(LifecycleFailure),
    DisplayChanged,
    DisplayUnknown(String),
    SettingsChanged,
    Geometry(PointerError),
    Device(std::io::Error),
}

/// Where accessibility reads come from: the session's bus, or in the test
/// harness a fixture desktop and never the user's applications.
#[derive(Debug)]
enum AccessibilitySource {
    Bus(AtspiBus),
    #[cfg(feature = "test-harness")]
    Fixture(FixtureTree),
}

impl AccessibilitySource {
    fn tree(&self) -> &dyn Tree {
        match self {
            Self::Bus(bus) => bus,
            #[cfg(feature = "test-harness")]
            Self::Fixture(fixture) => fixture,
        }
    }
}

#[derive(Debug)]
pub struct Dispatcher {
    mode: BackendMode,
    initialized: bool,
    negotiated_minor: Option<u16>,
    lifecycle: Option<Lifecycle>,
    /// Built on the first `display.snapshot`, never by this field at startup.
    /// Task 3.1 measured one baseline session-bus connection from desktop-state
    /// monitoring and exactly one additional connection for this reader.
    display: Option<DisplayReader>,
    /// Session and PipeWire loop, both created only by the first real capture.
    capture: Option<NativeCapture>,
    /// Created only by the first explicit native keyboard request. Capability
    /// probes and the default Python input selection never open `/dev/uinput`.
    keyboard: Option<(u64, Arc<dyn KeyboardService>)>,
    /// Created only by an explicit pointer request. The topology id and
    /// geometry are replaced together when the display layout changes.
    pointer: Option<(String, PointerConfig, Arc<dyn PointerService>)>,
    /// Connected by the first accessibility request, never at startup.
    accessibility: Option<AccessibilitySource>,
    state_dir: Option<PathBuf>,
}

impl Dispatcher {
    #[must_use]
    pub fn new(mode: BackendMode) -> Self {
        Self {
            mode,
            initialized: false,
            negotiated_minor: None,
            lifecycle: None,
            display: None,
            capture: None,
            keyboard: None,
            pointer: None,
            accessibility: None,
            state_dir: None,
        }
    }

    pub fn dispatch(&mut self, frame: Frame) -> Result<DispatchOutcome, ProtocolError> {
        let request = RequestHeader::from_value(frame.header)?;
        if request.id.is_empty() {
            return Err(ProtocolError::InvalidRequest);
        }
        if request.method.is_empty() {
            return Err(ProtocolError::InvalidRequest);
        }
        if request.binary_len != frame.binary.len() as u64 {
            return Err(ProtocolError::BinaryLengthMismatch {
                declared: request.binary_len,
                actual: frame.binary.len(),
            });
        }

        if request.protocol.major != PROTOCOL_MAJOR {
            return Ok(self.error(
                request.id,
                "UNSUPPORTED_PROTOCOL",
                format!(
                    "protocol major {} is unsupported; server major is {PROTOCOL_MAJOR}",
                    request.protocol.major
                ),
                Some(CloseConnection::IncompatibleProtocol),
            ));
        }

        if request.method == "initialize" {
            return Ok(self.initialize(request));
        }

        if !self.initialized {
            return Ok(self.error(
                request.id,
                "NOT_INITIALIZED",
                "initialize must be the first request",
                None,
            ));
        }

        if request.protocol.minor != self.negotiated_minor.unwrap_or(PROTOCOL_MINOR) {
            return Ok(self.error(
                request.id,
                "UNSUPPORTED_PROTOCOL_MINOR",
                "request minor version does not match the negotiated version",
                None,
            ));
        }

        // `clipboard.write` is the one request that carries bytes: clipboard
        // content does not fit, and must not be put, in a JSON header.
        if !frame.binary.is_empty() && request.method != "clipboard.write" {
            return Ok(self.error(
                request.id,
                "UNEXPECTED_BINARY",
                "control methods do not accept a binary payload",
                None,
            ));
        }

        let outcome = match request.method.as_str() {
            "ping" => self.success(request.id, ping_result(request.params), None),
            "capabilities" => self.success(request.id, self.mode.capabilities(), None),
            "display.snapshot" => self.display_snapshot(request.id),
            "capture.frame" => self.capture_frame(request.id, request.params),
            "capture.session_open" => self.capture_session_open(request.id, request.params),
            "input.keyboard.ensure" => self.keyboard_ensure(request.id, request.params),
            "input.keyboard.key" => {
                self.keyboard_action(request.id, request.params, KeyAction::Key)
            }
            "input.keyboard.key_down" => {
                self.keyboard_action(request.id, request.params, KeyAction::Down)
            }
            "input.keyboard.key_up" => {
                self.keyboard_action(request.id, request.params, KeyAction::Up)
            }
            "input.keyboard.held" => self.keyboard_held(request.id),
            "input.keyboard.release_all" => self.keyboard_release_all(request.id),
            "input.keyboard.take_auto_released" => self.keyboard_take_auto_released(request.id),
            "input.pointer.ensure" => self.pointer_ensure(request.id, request.params),
            "input.pointer.move" => self.pointer_move(request.id, request.params),
            "input.pointer.click" => self.pointer_click(request.id, request.params),
            "input.pointer.drag" => self.pointer_drag(request.id, request.params),
            "input.pointer.scroll" => self.pointer_scroll(request.id, request.params),
            "input.pointer.mouse_down" => self.pointer_button(request.id, request.params, true),
            "input.pointer.mouse_up" => self.pointer_button(request.id, request.params, false),
            "input.pointer.held" => self.pointer_held(request.id),
            "input.pointer.release_all" => self.pointer_release_all(request.id),
            "input.pointer.take_auto_released" => self.pointer_take_auto_released(request.id),
            "input.pointer.position" => self.pointer_position(request.id),
            "clipboard.read" => self.clipboard_read(request.id, request.params),
            "clipboard.write" => self.clipboard_write(request.id, request.params, frame.binary),
            "clipboard.clear" => self.clipboard_clear(request.id, request.params),
            "accessibility.dump" => self.accessibility_dump(request.id, request.params),
            "accessibility.windows" => {
                self.accessibility_read(request.id, request.params, |tree| {
                    accessibility::windows(tree)
                })
            }
            "accessibility.focused" => {
                self.accessibility_read(request.id, request.params, |tree| {
                    accessibility::focused(tree)
                })
            }
            #[cfg(feature = "test-harness")]
            "test.accessibility_desktop" => {
                self.test_accessibility_desktop(request.id, request.params)
            }
            "cancel" => match parse_cancel(request.params) {
                Ok(target_id) => self.success(
                    request.id,
                    json!({"target_id": target_id, "canceled": false}),
                    None,
                ),
                Err(message) => self.error(request.id, "INVALID_PARAMS", message, None),
            },
            "shutdown" => {
                self.close_keyboard();
                self.close_pointer();
                self.success(
                    request.id,
                    json!({"shutdown": true}),
                    Some(CloseConnection::Shutdown),
                )
            }
            #[cfg(feature = "test-harness")]
            "test.hold_resource" => match self.lifecycle.as_ref() {
                Some(lifecycle) => match lifecycle.open_test_resource() {
                    Ok(()) => self.success(request.id, json!({"open": true}), None),
                    Err(failure) => self.lease_error(request.id, failure),
                },
                None => self.lease_error(request.id, LeaseFailure::GrantRequired),
            },
            #[cfg(feature = "test-harness")]
            "test.resource_status" => self.success(
                request.id,
                json!({
                    "open": self.lifecycle
                        .as_ref()
                        .is_some_and(Lifecycle::test_resource_is_open),
                }),
                None,
            ),
            _ => self.error(
                request.id,
                "UNKNOWN_METHOD",
                format!("method '{}' is not available", request.method),
                None,
            ),
        };
        Ok(outcome)
    }

    fn initialize(&mut self, request: RequestHeader) -> DispatchOutcome {
        if self.initialized {
            return self.error(
                request.id,
                "ALREADY_INITIALIZED",
                "initialize may only be called once",
                None,
            );
        }
        if request.binary_len != 0 {
            return self.error(
                request.id,
                "UNEXPECTED_BINARY",
                "initialize does not accept a binary payload",
                None,
            );
        }

        let params = match serde_json::from_value::<InitializeParams>(request.params) {
            Ok(params) => params,
            Err(error) => {
                return self.error(
                    request.id,
                    "INVALID_PARAMS",
                    format!("invalid initialize parameters: {error}"),
                    None,
                );
            }
        };
        if params.client_version.is_empty() {
            return self.error(
                request.id,
                "INVALID_PARAMS",
                "client_version must be nonempty",
                None,
            );
        }
        if !Path::new(&params.state_dir).is_absolute()
            || !Path::new(&params.runtime_dir).is_absolute()
        {
            return self.error(
                request.id,
                "INVALID_PARAMS",
                "state_dir and runtime_dir must be absolute paths",
                None,
            );
        }
        if !params.supported_minor.contains(&PROTOCOL_MINOR) {
            return self.error(
                request.id,
                "UNSUPPORTED_PROTOCOL",
                "client and server have no shared protocol minor version",
                Some(CloseConnection::IncompatibleProtocol),
            );
        }

        let lifecycle = match &self.mode {
            BackendMode::Production { .. } => Lifecycle::start(Path::new(&params.state_dir)),
            #[cfg(feature = "test-harness")]
            BackendMode::DeterministicTest => Lifecycle::start_with_provider(
                Path::new(&params.state_dir),
                Arc::new(DeterministicDesktopState),
            ),
        };
        let lifecycle = match lifecycle {
            Ok(lifecycle) => lifecycle,
            Err(_) => {
                return self.error(
                    request.id,
                    "BACKEND_UNAVAILABLE",
                    "native lease watchdog could not start",
                    None,
                );
            }
        };
        let lease_bound = lifecycle.lease_bound();
        self.lifecycle = Some(lifecycle);
        self.state_dir = Some(PathBuf::from(&params.state_dir));
        self.initialized = true;
        self.negotiated_minor = Some(PROTOCOL_MINOR);
        self.success(
            request.id,
            json!({
                "instance_id": self.mode.instance_id(),
                "native_version": crate::build_info::VERSION,
                "build_id": crate::build_info::build_id(),
                "platform": self.mode.platform(),
                "features": self.mode.features(),
                "lease_bound": lease_bound,
            }),
            None,
        )
    }

    /// Read-only display metadata: no pixels, no grant, no device. Mirrors the
    /// host's `screen_info`, which is likewise outside the desktop grant.
    fn display_snapshot(&mut self, id: String) -> DispatchOutcome {
        match self.current_display_snapshot() {
            Ok(snapshot) => {
                let (width, height) = pcbridge_core::display::canvas_size(&snapshot.monitors);
                let monitors: Vec<Value> = snapshot
                    .monitors
                    .iter()
                    .map(|monitor| {
                        json!({
                            "index": monitor.index,
                            "connector": monitor.connector,
                            "x": monitor.x,
                            "y": monitor.y,
                            "width": monitor.width,
                            "height": monitor.height,
                            "scale": monitor.scale,
                            "primary": monitor.primary,
                            "name": monitor.name,
                            "transform": monitor.transform,
                            "serial": monitor.serial,
                        })
                    })
                    .collect();
                self.success(
                    id,
                    json!({
                        "topology_id": snapshot.topology_id,
                        "canvas": [width, height],
                        "monitors": monitors,
                    }),
                    None,
                )
            }
            Err(err) => self.error(id, "DISPLAY_MAPPING_UNKNOWN", err.to_string(), None),
        }
    }

    fn current_display_snapshot(&mut self) -> Result<DisplaySnapshot, String> {
        if self.display.is_none() {
            self.display = Some(
                DisplayReader::connect()
                    .map_err(|error| format!("display config unavailable: {error}"))?,
            );
        }
        self.display
            .as_ref()
            .expect("display reader was just constructed")
            .snapshot()
            .map_err(|error| error.to_string())
    }

    fn success(
        &self,
        id: String,
        result: Value,
        close: Option<CloseConnection>,
    ) -> DispatchOutcome {
        DispatchOutcome {
            response: ResponseHeader::success(id, result),
            binary: Vec::new(),
            close,
        }
    }

    fn success_binary(&self, id: String, result: Value, binary: Vec<u8>) -> DispatchOutcome {
        DispatchOutcome {
            response: ResponseHeader::success_with_binary(id, result, binary.len() as u64),
            binary,
            close: None,
        }
    }

    fn error(
        &self,
        id: String,
        code: &str,
        message: impl Into<String>,
        close: Option<CloseConnection>,
    ) -> DispatchOutcome {
        DispatchOutcome {
            response: ResponseHeader::error(id, ErrorBody::protocol(code, message)),
            binary: Vec::new(),
            close,
        }
    }

    fn typed_error(&self, id: String, error: ErrorBody) -> DispatchOutcome {
        DispatchOutcome {
            response: ResponseHeader::error(id, error),
            binary: Vec::new(),
            close: None,
        }
    }

    #[cfg(feature = "test-harness")]
    fn lease_error(&self, id: String, failure: LeaseFailure) -> DispatchOutcome {
        DispatchOutcome {
            response: ResponseHeader::error(
                id,
                ErrorBody {
                    code: failure.code().to_owned(),
                    message: "desktop grant is unavailable or revoked".to_owned(),
                    retryable: true,
                    category: "safety".to_owned(),
                },
            ),
            binary: Vec::new(),
            close: None,
        }
    }

    fn capture_frame(&mut self, id: String, params: Value) -> DispatchOutcome {
        let params = match serde_json::from_value::<CaptureFrameParams>(params) {
            Ok(params) => params,
            Err(error) => {
                return self.error(
                    id,
                    "INVALID_PARAMS",
                    format!("invalid capture parameters: {error}"),
                    None,
                );
            }
        };
        if let Err(message) = params.validate() {
            return self.error(id, "INVALID_PARAMS", message, None);
        }

        match &self.mode {
            #[cfg(feature = "test-harness")]
            BackendMode::DeterministicTest => {
                let frame = pcbridge_core::frame::RgbaFrame {
                    width: 2,
                    height: 1,
                    id: pcbridge_core::frame::FrameId {
                        sequence: 42,
                        captured_at_ns: 4242,
                    },
                    pixels: vec![0x11, 0x22, 0x33, 0xFF, 0xAA, 0xBB, 0xCC, 0xFF],
                };
                match frame.to_png() {
                    Ok(png) => self.success_binary(
                        id,
                        json!({
                            "display_id": params.display_id,
                            "topology_id": params.topology_id,
                            "session_id": params.session_id,
                            "frame_sequence": frame.id.sequence,
                            "frame_timestamp_ns": frame.id.captured_at_ns,
                            "frame_identity_source": "test_fixture",
                            "pixel_size": [frame.width, frame.height],
                            "desktop_rect": [0, 0, frame.width, frame.height],
                            "stale_frames": 0,
                            "include_pointer": params.include_pointer,
                            "revoke_epoch": params.revoke_epoch,
                            "backend": "test.fake",
                            "mime_type": "image/png",
                        }),
                        png,
                    ),
                    Err(error) => self.error(id, "CAPTURE_FAILED", error.to_string(), None),
                }
            }
            BackendMode::Production { .. } => self.capture_frame_production(id, params),
        }
    }

    fn capture_frame_production(
        &mut self,
        id: String,
        params: CaptureFrameParams,
    ) -> DispatchOutcome {
        let Some(connector) = params.display_id.strip_prefix("mutter:") else {
            return self.error(
                id,
                "INVALID_PARAMS",
                "production display_id must start with 'mutter:'",
                None,
            );
        };
        let connector = connector.to_owned();
        let lifecycle = self
            .lifecycle
            .as_ref()
            .expect("initialized dispatcher has a lifecycle");
        if !lifecycle.matches_token(&params.grant_id, params.revoke_epoch) {
            return self.typed_error(
                id,
                ErrorBody {
                    code: "REVOKED".to_owned(),
                    message: "capture request does not match the bound desktop grant".to_owned(),
                    retryable: false,
                    category: "safety".to_owned(),
                },
            );
        }

        let snapshot = match self.current_display_snapshot() {
            Ok(snapshot) => snapshot,
            Err(message) => {
                return self.typed_error(
                    id,
                    ErrorBody {
                        code: "DISPLAY_MAPPING_UNKNOWN".to_owned(),
                        message,
                        retryable: true,
                        category: "capture".to_owned(),
                    },
                );
            }
        };
        if self.capture.is_none() {
            let lifecycle = self
                .lifecycle
                .as_ref()
                .expect("initialized dispatcher has a lifecycle");
            match NativeCapture::connect(lifecycle) {
                Ok(capture) => self.capture = Some(capture),
                Err(error) => return self.native_capture_error(id, error),
            }
        }
        let lifecycle = self
            .lifecycle
            .as_ref()
            .expect("initialized dispatcher has a lifecycle");
        let capture = self
            .capture
            .as_ref()
            .expect("capture resources were just constructed");
        match capture.capture(
            &snapshot,
            &params.topology_id,
            &connector,
            params.include_pointer,
            std::time::Duration::from_millis(params.timeout_ms),
            lifecycle,
        ) {
            Ok(captured) => {
                let image = captured.image;
                self.success_binary(
                    id,
                    json!({
                        "display_id": params.display_id,
                        "topology_id": params.topology_id,
                        "session_id": params.session_id,
                        "frame_sequence": image.id.sequence,
                        "frame_timestamp_ns": image.id.captured_at_ns,
                        "frame_identity_source": image.identity_source.as_str(),
                        "pixel_size": [image.width, image.height],
                        "desktop_rect": [
                            captured.x,
                            captured.y,
                            captured.expected_width,
                            captured.expected_height,
                        ],
                        "stale_frames": image.stale_frames,
                        "include_pointer": params.include_pointer,
                        "revoke_epoch": params.revoke_epoch,
                        "wait_ms": image.waited.as_secs_f64() * 1000.0,
                        "encode_ms": image.encoded_in.as_secs_f64() * 1000.0,
                        "backend": "linux.mutter.pipewire",
                        "mime_type": "image/png",
                    }),
                    image.png,
                )
            }
            Err(error) => self.native_capture_error(id, error),
        }
    }

    fn capture_session_open(&mut self, id: String, params: Value) -> DispatchOutcome {
        let params = match serde_json::from_value::<SessionOpenParams>(params) {
            Ok(params) => params,
            Err(error) => {
                return self.error(
                    id,
                    "INVALID_PARAMS",
                    format!("invalid session parameters: {error}"),
                    None,
                );
            }
        };
        if let Err(message) = params.validate() {
            return self.error(id, "INVALID_PARAMS", message, None);
        }

        match &self.mode {
            #[cfg(feature = "test-harness")]
            BackendMode::DeterministicTest => self.success(
                id,
                json!({
                    "session_id": params.session_id,
                    "topology_id": params.topology_id,
                    "outcome": "opened",
                    "monitors": ["TEST-1"],
                    "include_pointer": params.include_pointer,
                    "backend": "test.fake",
                }),
                None,
            ),
            BackendMode::Production { .. } => self.capture_session_open_production(id, params),
        }
    }

    /// Open the share without reading a frame (Task 4.3).
    ///
    /// `desktop_unlock` reaches this, so GNOME's sharing indicator appears with
    /// the grant, when the Python path has always shown it. The grant, layout
    /// and session rules are the ones `capture.frame` uses, because both go
    /// through `NativeCapture::open_session`.
    fn capture_session_open_production(
        &mut self,
        id: String,
        params: SessionOpenParams,
    ) -> DispatchOutcome {
        let lifecycle = self
            .lifecycle
            .as_ref()
            .expect("initialized dispatcher has a lifecycle");
        if !lifecycle.matches_token(&params.grant_id, params.revoke_epoch) {
            return self.typed_error(
                id,
                ErrorBody {
                    code: "REVOKED".to_owned(),
                    message: "session request does not match the bound desktop grant".to_owned(),
                    retryable: false,
                    category: "safety".to_owned(),
                },
            );
        }

        let snapshot = match self.current_display_snapshot() {
            Ok(snapshot) => snapshot,
            Err(message) => {
                return self.typed_error(
                    id,
                    ErrorBody {
                        code: "DISPLAY_MAPPING_UNKNOWN".to_owned(),
                        message,
                        retryable: true,
                        category: "capture".to_owned(),
                    },
                );
            }
        };
        if self.capture.is_none() {
            let lifecycle = self
                .lifecycle
                .as_ref()
                .expect("initialized dispatcher has a lifecycle");
            match NativeCapture::connect(lifecycle) {
                Ok(capture) => self.capture = Some(capture),
                Err(error) => return self.native_capture_error(id, error),
            }
        }
        let lifecycle = self
            .lifecycle
            .as_ref()
            .expect("initialized dispatcher has a lifecycle");
        let capture = self
            .capture
            .as_ref()
            .expect("capture resources were just constructed");
        match capture.open_session(
            &snapshot,
            &params.topology_id,
            params.include_pointer,
            lifecycle,
        ) {
            Ok(outcome) => {
                let outcome = match outcome {
                    OpenOutcome::Opened => "opened",
                    OpenOutcome::Reused => "reused",
                    OpenOutcome::Recreated => "recreated",
                };
                let monitors: Vec<&str> = snapshot
                    .monitors
                    .iter()
                    .map(|monitor| monitor.connector.as_str())
                    .collect();
                self.success(
                    id,
                    json!({
                        "session_id": params.session_id,
                        "topology_id": params.topology_id,
                        "outcome": outcome,
                        "monitors": monitors,
                        "include_pointer": params.include_pointer,
                        "backend": "linux.mutter.pipewire",
                    }),
                    None,
                )
            }
            Err(error) => self.native_capture_error(id, error),
        }
    }

    fn keyboard_ensure(&mut self, id: String, params: Value) -> DispatchOutcome {
        let params = match serde_json::from_value::<KeyboardGrantParams>(params) {
            Ok(params) => params,
            Err(error) => {
                return self.error(
                    id,
                    "INVALID_PARAMS",
                    format!("invalid keyboard parameters: {error}"),
                    None,
                );
            }
        };
        if let Err(message) = params.validate() {
            return self.error(id, "INVALID_PARAMS", message, None);
        }
        if let Err(failure) = self.validate_keyboard_grant(&params) {
            return self.keyboard_lifecycle_error(id, failure);
        }
        match self.ensure_keyboard(params.hold_max_seconds) {
            Ok((keyboard, waited_seconds)) => {
                // Device creation includes a 1.2 s compositor settle. The
                // grant can be revoked during that wait, so validate again
                // before reporting the keyboard as ready.
                if let Err(failure) = self.validate_keyboard_grant(&params) {
                    self.close_keyboard();
                    return self.keyboard_lifecycle_error(id, failure);
                }
                self.success(
                    id,
                    json!({
                        "already": waited_seconds == 0.0,
                        "waited_seconds": waited_seconds,
                        "held": keyboard.held(),
                        "backend": "linux.uinput.native",
                    }),
                    None,
                )
            }
            Err(error) => self.keyboard_setup_error(id, error),
        }
    }

    fn keyboard_action(&mut self, id: String, params: Value, action: KeyAction) -> DispatchOutcome {
        let params = match serde_json::from_value::<KeyboardActionParams>(params) {
            Ok(params) => params,
            Err(error) => {
                return self.error(
                    id,
                    "INVALID_PARAMS",
                    format!("invalid keyboard parameters: {error}"),
                    None,
                );
            }
        };
        if let Err(message) = params.validate() {
            return self.error(id, "INVALID_PARAMS", message, None);
        }
        if let Err(failure) = self.validate_keyboard_grant(&params.grant) {
            return self.keyboard_lifecycle_error(id, failure);
        }
        let keyboard = match self.ensure_keyboard(params.grant.hold_max_seconds) {
            Ok((keyboard, _)) => keyboard,
            Err(error) => return self.keyboard_setup_error(id, error),
        };
        // The first request may spend DEVICE_SETTLE constructing the device.
        // Recheck after that wait so a revoke cannot be followed by a key.
        if let Err(failure) = self.validate_keyboard_grant(&params.grant) {
            self.close_keyboard();
            return self.keyboard_lifecycle_error(id, failure);
        }
        let result = match action {
            KeyAction::Key => keyboard.key(&params.combo),
            KeyAction::Down => keyboard.key_down(&params.combo),
            KeyAction::Up => keyboard.key_up(&params.combo),
        };
        match result {
            Ok(()) => self.success(
                id,
                json!({
                    "held": keyboard.held(),
                    "backend": "linux.uinput.native",
                }),
                None,
            ),
            Err(error) => self.keyboard_operation_error(id, error),
        }
    }

    fn keyboard_held(&self, id: String) -> DispatchOutcome {
        self.success(
            id,
            json!({
                "held": self
                    .keyboard
                    .as_ref()
                    .map_or_else(Vec::new, |(_, keyboard)| keyboard.held()),
                "backend": "linux.uinput.native",
            }),
            None,
        )
    }

    fn keyboard_release_all(&self, id: String) -> DispatchOutcome {
        let Some((_, keyboard)) = self.keyboard.as_ref() else {
            return self.success(
                id,
                json!({"released": [], "backend": "linux.uinput.native"}),
                None,
            );
        };
        match keyboard.release_all() {
            Ok(released) => self.success(
                id,
                json!({"released": released, "backend": "linux.uinput.native"}),
                None,
            ),
            Err(error) => self.keyboard_operation_error(id, error),
        }
    }

    fn keyboard_take_auto_released(&self, id: String) -> DispatchOutcome {
        self.success(
            id,
            json!({
                "released": self.keyboard.as_ref().map_or_else(
                    Vec::new,
                    |(_, keyboard)| keyboard.take_auto_released(),
                ),
                "backend": "linux.uinput.native",
            }),
            None,
        )
    }

    /// The programs a clipboard request runs.
    ///
    /// The test harness never reaches the user's clipboard: it runs only the
    /// programs a test names, and refuses when none are named.
    fn clipboard(&self) -> Result<Clipboard<SystemPrograms>, ErrorBody> {
        match &self.mode {
            BackendMode::Production { .. } => Ok(Clipboard::new(SystemPrograms::default())),
            #[cfg(feature = "test-harness")]
            BackendMode::DeterministicTest => {
                match (
                    std::env::var_os("PCBRIDGE_TEST_WL_PASTE"),
                    std::env::var_os("PCBRIDGE_TEST_WL_COPY"),
                ) {
                    (Some(paste), Some(copy)) => Ok(Clipboard::new(SystemPrograms::with_programs(
                        paste,
                        copy,
                        clipboard::PROGRAM_TIMEOUT,
                    ))),
                    _ => Err(ErrorBody {
                        code: "UNSUPPORTED".to_owned(),
                        message: "the test harness has no clipboard programs".to_owned(),
                        retryable: false,
                        category: "capability".to_owned(),
                    }),
                }
            }
        }
    }

    fn clipboard_grant<T: DeserializeOwned + ClipboardGrant>(
        &self,
        id: &str,
        params: Value,
    ) -> Result<(T, Clipboard<SystemPrograms>), Box<DispatchOutcome>> {
        let params = serde_json::from_value::<T>(params).map_err(|error| {
            Box::new(self.error(
                id.to_owned(),
                "INVALID_PARAMS",
                format!("invalid clipboard parameters: {error}"),
                None,
            ))
        })?;
        params.validate().map_err(|message| {
            Box::new(self.error(id.to_owned(), "INVALID_PARAMS", message, None))
        })?;
        self.validate_grant_token(params.grant_id(), params.revoke_epoch())
            .map_err(|failure| Box::new(self.keyboard_lifecycle_error(id.to_owned(), failure)))?;
        let clipboard = self
            .clipboard()
            .map_err(|error| Box::new(self.typed_error(id.to_owned(), error)))?;
        Ok((params, clipboard))
    }

    /// The first offered type and its bytes, as the response's binary payload.
    fn clipboard_read(&self, id: String, params: Value) -> DispatchOutcome {
        let (params, clipboard) = match self.clipboard_grant::<ClipboardGrantParams>(&id, params) {
            Ok(ready) => ready,
            Err(outcome) => return *outcome,
        };
        let saved = clipboard.save();
        // Reading can take up to the program timeout. A revoke in that window
        // must not be answered with the user's clipboard.
        if let Err(failure) = self.validate_grant_token(&params.grant_id, params.revoke_epoch) {
            return self.keyboard_lifecycle_error(id, failure);
        }
        match saved {
            Ok(Some(saved)) => self.success_binary(
                id,
                json!({"empty": false, "mime": saved.mime, "backend": CLIPBOARD_BACKEND}),
                saved.data,
            ),
            Ok(None) => self.success(
                id,
                json!({"empty": true, "mime": null, "backend": CLIPBOARD_BACKEND}),
                None,
            ),
            Err(error) => self.clipboard_error(id, error),
        }
    }

    fn clipboard_write(&self, id: String, params: Value, data: Vec<u8>) -> DispatchOutcome {
        let (params, clipboard) = match self.clipboard_grant::<ClipboardWriteParams>(&id, params) {
            Ok(ready) => ready,
            Err(outcome) => return *outcome,
        };
        match clipboard.put(&params.mime, &data) {
            Ok(()) => self.success(
                id,
                json!({"written": data.len(), "backend": CLIPBOARD_BACKEND}),
                None,
            ),
            Err(error) => self.clipboard_error(id, error),
        }
    }

    fn clipboard_clear(&self, id: String, params: Value) -> DispatchOutcome {
        let (_params, clipboard) = match self.clipboard_grant::<ClipboardGrantParams>(&id, params) {
            Ok(ready) => ready,
            Err(outcome) => return *outcome,
        };
        match clipboard.clear() {
            Ok(()) => self.success(
                id,
                json!({"cleared": true, "backend": CLIPBOARD_BACKEND}),
                None,
            ),
            Err(error) => self.clipboard_error(id, error),
        }
    }

    fn clipboard_error(&self, id: String, error: ClipboardError) -> DispatchOutcome {
        let (code, category, retryable) = match &error {
            ClipboardError::InvalidMime => {
                return self.error(id, "INVALID_PARAMS", error.to_string(), None);
            }
            ClipboardError::Missing { .. } => ("DEPENDENCY_MISSING", "capability", false),
            ClipboardError::Timeout { .. } => ("TIMEOUT", "execution", true),
            ClipboardError::Failed { .. } => ("EXECUTION_UNKNOWN", "execution", false),
            ClipboardError::TooLarge { .. } => ("UNSUPPORTED", "capability", false),
            ClipboardError::Io { .. } => ("BACKEND_UNAVAILABLE", "capability", true),
        };
        self.typed_error(
            id,
            ErrorBody {
                code: code.to_owned(),
                message: error.to_string(),
                retryable,
                category: category.to_owned(),
            },
        )
    }

    /// The tree an accessibility request reads, connected on first use.
    ///
    /// The harness never reaches the user's applications: it reads only the
    /// fixture desktop a test names, and refuses when none is named.
    fn accessibility_tree(&mut self) -> Result<&dyn Tree, ErrorBody> {
        if self.accessibility.is_none() {
            let source = match &self.mode {
                BackendMode::Production { .. } => AtspiBus::connect()
                    .map(AccessibilitySource::Bus)
                    .map_err(|error| accessibility_error_body(&error))?,
                #[cfg(feature = "test-harness")]
                BackendMode::DeterministicTest => {
                    let desktop = std::env::var("PCBRIDGE_TEST_A11Y_DESKTOP").unwrap_or_default();
                    AccessibilitySource::Fixture(load_fixture_desktop(&desktop)?)
                }
            };
            self.accessibility = Some(source);
        }
        Ok(self
            .accessibility
            .as_ref()
            .expect("accessibility source was just set")
            .tree())
    }

    fn accessibility_grant<T: DeserializeOwned + AccessibilityGrant>(
        &self,
        id: &str,
        params: Value,
    ) -> Result<T, Box<DispatchOutcome>> {
        let params = serde_json::from_value::<T>(params).map_err(|error| {
            Box::new(self.error(
                id.to_owned(),
                "INVALID_PARAMS",
                format!("invalid accessibility parameters: {error}"),
                None,
            ))
        })?;
        validate_grant_id(params.grant_id()).map_err(|message| {
            Box::new(self.error(id.to_owned(), "INVALID_PARAMS", message, None))
        })?;
        self.validate_grant_token(params.grant_id(), params.revoke_epoch())
            .map_err(|failure| Box::new(self.keyboard_lifecycle_error(id.to_owned(), failure)))?;
        Ok(params)
    }

    /// Answer with what was read, unless the grant ended while reading:
    /// the tree names what is on screen, and a revoke in that window must
    /// not be answered with it.
    fn accessibility_reply(
        &self,
        id: String,
        grant: (&str, u64),
        read: Result<Value, AccessibilityError>,
    ) -> DispatchOutcome {
        if let Err(failure) = self.validate_grant_token(grant.0, grant.1) {
            return self.keyboard_lifecycle_error(id, failure);
        }
        match read {
            Ok(result) => self.success(id, result, None),
            Err(error) => self.typed_error(id, accessibility_error_body(&error)),
        }
    }

    fn accessibility_dump(&mut self, id: String, params: Value) -> DispatchOutcome {
        let params = match self.accessibility_grant::<AccessibilityDumpParams>(&id, params) {
            Ok(params) => params,
            Err(outcome) => return *outcome,
        };
        let deadline_ms = match params.deadline_ms {
            0 => DUMP_DEADLINE_MS,
            value => value.min(DUMP_DEADLINE_MS_MAX),
        };
        let request = DumpRequest {
            target: match params.target.trim() {
                "" => "focused".to_owned(),
                target => target.to_owned(),
            },
            interactive_only: params.interactive_only,
            max_nodes: match params.max_nodes {
                0 => accessibility::DEFAULT_MAX_NODES,
                value => usize::try_from(value.min(MAX_DUMP_NODES))
                    .unwrap_or(accessibility::DEFAULT_MAX_NODES),
            },
            deadline: Some(std::time::Instant::now() + Duration::from_millis(deadline_ms)),
            deadline_seconds: deadline_ms.div_ceil(1000),
        };
        let read = match self.accessibility_tree() {
            Ok(tree) => accessibility::dump(tree, &request),
            Err(error) => return self.typed_error(id, error),
        };
        self.accessibility_reply(id, (&params.grant_id, params.revoke_epoch), read)
    }

    fn accessibility_read(
        &mut self,
        id: String,
        params: Value,
        read: fn(&dyn Tree) -> Result<Value, AccessibilityError>,
    ) -> DispatchOutcome {
        let params = match self.accessibility_grant::<AccessibilityGrantParams>(&id, params) {
            Ok(params) => params,
            Err(outcome) => return *outcome,
        };
        let result = match self.accessibility_tree() {
            Ok(tree) => read(tree),
            Err(error) => return self.typed_error(id, error),
        };
        self.accessibility_reply(id, (&params.grant_id, params.revoke_epoch), result)
    }

    /// Harness only: read another fixture desktop from now on, the way an
    /// application changes its tree between a dump and an action.
    #[cfg(feature = "test-harness")]
    fn test_accessibility_desktop(&mut self, id: String, params: Value) -> DispatchOutcome {
        let desktop = params
            .get("desktop")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        match load_fixture_desktop(&desktop) {
            Ok(tree) => {
                self.accessibility = Some(AccessibilitySource::Fixture(tree));
                self.success(id, json!({"desktop": desktop}), None)
            }
            Err(error) => self.typed_error(id, error),
        }
    }

    fn validate_grant_token(
        &self,
        grant_id: &str,
        revoke_epoch: u64,
    ) -> Result<(), LifecycleFailure> {
        let lifecycle = self
            .lifecycle
            .as_ref()
            .expect("initialized dispatcher has a lifecycle");
        if !lifecycle.matches_token(grant_id, revoke_epoch) {
            return Err(LifecycleFailure::Revoked);
        }
        lifecycle.validate_now()
    }

    fn validate_keyboard_grant(
        &self,
        params: &KeyboardGrantParams,
    ) -> Result<(), LifecycleFailure> {
        let lifecycle = self
            .lifecycle
            .as_ref()
            .expect("initialized dispatcher has a lifecycle");
        if !lifecycle.matches_token(&params.grant_id, params.revoke_epoch) {
            return Err(LifecycleFailure::Revoked);
        }
        lifecycle.validate_now()
    }

    fn keyboard_lifecycle_error(&self, id: String, failure: LifecycleFailure) -> DispatchOutcome {
        self.typed_error(
            id,
            ErrorBody {
                code: failure.code().to_owned(),
                message: "the desktop grant or session is not usable".to_owned(),
                retryable: failure != LifecycleFailure::Revoked,
                category: "safety".to_owned(),
            },
        )
    }

    fn ensure_keyboard(
        &mut self,
        hold_max_seconds: u64,
    ) -> Result<(Arc<dyn KeyboardService>, f64), KeyboardSetupError> {
        if self
            .keyboard
            .as_ref()
            .is_some_and(|(_, keyboard)| keyboard.is_closed())
        {
            self.keyboard.take();
        }
        if let Some((configured, keyboard)) = self.keyboard.as_ref() {
            if *configured != hold_max_seconds {
                return Err(KeyboardSetupError::HoldChanged);
            }
            return Ok((Arc::clone(keyboard), 0.0));
        }

        let (keyboard, settle): (Arc<dyn KeyboardService>, Duration) = match &self.mode {
            BackendMode::Production { .. } => {
                let device = EvdevKeyboardDevice::create().map_err(KeyboardSetupError::Device)?;
                (
                    Arc::new(NativeKeyboard::new(
                        device,
                        SystemClock::default(),
                        Duration::from_secs(hold_max_seconds),
                    )),
                    DEVICE_SETTLE,
                )
            }
            #[cfg(feature = "test-harness")]
            BackendMode::DeterministicTest => (
                Arc::new(Keyboard::new(
                    NullKeyboardDevice,
                    SystemClock::default(),
                    Duration::from_secs(hold_max_seconds),
                )),
                Duration::ZERO,
            ),
        };
        let lifecycle = self
            .lifecycle
            .as_ref()
            .expect("initialized dispatcher has a lifecycle");
        lifecycle.register_fail_closed(Arc::new(KeyboardFailClosed(Arc::downgrade(&keyboard))));
        self.keyboard = Some((hold_max_seconds, Arc::clone(&keyboard)));
        // Register before waiting: a revoke during device settling reaches
        // this resource, and the caller revalidates before any key write.
        std::thread::sleep(settle);
        Ok((keyboard, settle.as_secs_f64()))
    }

    fn keyboard_setup_error(&self, id: String, error: KeyboardSetupError) -> DispatchOutcome {
        match error {
            KeyboardSetupError::HoldChanged => self.error(
                id,
                "INVALID_PARAMS",
                "hold_max_seconds cannot change while the native keyboard is open",
                None,
            ),
            KeyboardSetupError::Device(error) => {
                let (code, category, retryable) = match error.kind() {
                    std::io::ErrorKind::NotFound => ("DEPENDENCY_MISSING", "capability", false),
                    std::io::ErrorKind::PermissionDenied => {
                        ("DEVICE_NOT_GRANTED", "permission", true)
                    }
                    _ => ("BACKEND_UNAVAILABLE", "capability", true),
                };
                self.typed_error(
                    id,
                    ErrorBody {
                        code: code.to_owned(),
                        message: format!("native keyboard could not open /dev/uinput: {error}"),
                        retryable,
                        category: category.to_owned(),
                    },
                )
            }
        }
    }

    fn keyboard_operation_error(&self, id: String, error: KeyboardError) -> DispatchOutcome {
        match error {
            KeyboardError::UnknownKey(_) | KeyboardError::EmptyCombo => {
                self.error(id, "INVALID_PARAMS", error.to_string(), None)
            }
            KeyboardError::Closed => self.typed_error(
                id,
                ErrorBody {
                    code: "BACKEND_UNAVAILABLE".to_owned(),
                    message: error.to_string(),
                    retryable: true,
                    category: "capability".to_owned(),
                },
            ),
            KeyboardError::Device(source) => self.typed_error(
                id,
                ErrorBody {
                    code: "EXECUTION_UNKNOWN".to_owned(),
                    message: format!("native keyboard write failed: {source}"),
                    retryable: false,
                    category: "execution".to_owned(),
                },
            ),
        }
    }

    fn close_keyboard(&mut self) {
        if let Some((_, keyboard)) = self.keyboard.take() {
            let _ = keyboard.close();
        }
    }

    fn pointer_ensure(&mut self, id: String, params: Value) -> DispatchOutcome {
        let params = match parse_pointer_params::<PointerGrantParams>(params, &[]) {
            Ok(params) => params,
            Err(error) => {
                return self.error(
                    id,
                    "INVALID_PARAMS",
                    format!("invalid pointer parameters: {error}"),
                    None,
                );
            }
        };
        if let Err(message) = params.validate() {
            return self.error(id, "INVALID_PARAMS", message, None);
        }
        match self.pointer_for_request(&params) {
            Ok((pointer, waited_seconds)) => self.success(
                id,
                json!({
                    "already": waited_seconds == 0.0,
                    "waited_seconds": waited_seconds,
                    "position": pointer.position(),
                    "held": pointer.held(),
                    "backend": "linux.uinput.native",
                }),
                None,
            ),
            Err(error) => self.pointer_request_error(id, error),
        }
    }

    fn pointer_move(&mut self, id: String, params: Value) -> DispatchOutcome {
        let params = match parse_pointer_params::<PointerMoveParams>(params, &["x", "y", "smooth"])
        {
            Ok(params) => params,
            Err(error) => {
                return self.error(
                    id,
                    "INVALID_PARAMS",
                    format!("invalid pointer parameters: {error}"),
                    None,
                );
            }
        };
        if let Err(message) = params.grant.validate() {
            return self.error(id, "INVALID_PARAMS", message, None);
        }
        let pointer = match self.pointer_for_request(&params.grant) {
            Ok((pointer, _)) => pointer,
            Err(error) => return self.pointer_request_error(id, error),
        };
        match pointer.move_to(params.x, params.y, params.smooth, 1) {
            Ok(position) => self.success(
                id,
                json!({"position": position, "backend": "linux.uinput.native"}),
                None,
            ),
            Err(error) => self.pointer_operation_error(id, error),
        }
    }

    fn pointer_click(&mut self, id: String, params: Value) -> DispatchOutcome {
        let params = match parse_pointer_params::<PointerClickParams>(params, &["button", "count"])
        {
            Ok(params) => params,
            Err(error) => {
                return self.error(
                    id,
                    "INVALID_PARAMS",
                    format!("invalid pointer parameters: {error}"),
                    None,
                );
            }
        };
        if let Err(message) = params.grant.validate() {
            return self.error(id, "INVALID_PARAMS", message, None);
        }
        let pointer = match self.pointer_for_request(&params.grant) {
            Ok((pointer, _)) => pointer,
            Err(error) => return self.pointer_request_error(id, error),
        };
        match pointer.click(&params.button, params.count) {
            Ok(()) => self.success(
                id,
                json!({"held": pointer.held(), "backend": "linux.uinput.native"}),
                None,
            ),
            Err(error) => self.pointer_operation_error(id, error),
        }
    }

    fn pointer_drag(&mut self, id: String, params: Value) -> DispatchOutcome {
        let params = match parse_pointer_params::<PointerDragParams>(
            params,
            &["x1", "y1", "x2", "y2", "button"],
        ) {
            Ok(params) => params,
            Err(error) => {
                return self.error(
                    id,
                    "INVALID_PARAMS",
                    format!("invalid pointer parameters: {error}"),
                    None,
                );
            }
        };
        if let Err(message) = params.grant.validate() {
            return self.error(id, "INVALID_PARAMS", message, None);
        }
        let pointer = match self.pointer_for_request(&params.grant) {
            Ok((pointer, _)) => pointer,
            Err(error) => return self.pointer_request_error(id, error),
        };
        match pointer.drag(params.x1, params.y1, params.x2, params.y2, &params.button) {
            Ok(()) => self.success(
                id,
                json!({"position": pointer.position(), "backend": "linux.uinput.native"}),
                None,
            ),
            Err(error) => self.pointer_operation_error(id, error),
        }
    }

    fn pointer_scroll(&mut self, id: String, params: Value) -> DispatchOutcome {
        let params =
            match parse_pointer_params::<PointerScrollParams>(params, &["amount", "horizontal"]) {
                Ok(params) => params,
                Err(error) => {
                    return self.error(
                        id,
                        "INVALID_PARAMS",
                        format!("invalid pointer parameters: {error}"),
                        None,
                    );
                }
            };
        if let Err(message) = params.grant.validate() {
            return self.error(id, "INVALID_PARAMS", message, None);
        }
        let pointer = match self.pointer_for_request(&params.grant) {
            Ok((pointer, _)) => pointer,
            Err(error) => return self.pointer_request_error(id, error),
        };
        match pointer.scroll(params.amount, params.horizontal) {
            Ok(()) => self.success(
                id,
                json!({"position": pointer.position(), "backend": "linux.uinput.native"}),
                None,
            ),
            Err(error) => self.pointer_operation_error(id, error),
        }
    }

    fn pointer_button(&mut self, id: String, params: Value, down: bool) -> DispatchOutcome {
        let params = match parse_pointer_params::<PointerButtonParams>(params, &["button"]) {
            Ok(params) => params,
            Err(error) => {
                return self.error(
                    id,
                    "INVALID_PARAMS",
                    format!("invalid pointer parameters: {error}"),
                    None,
                );
            }
        };
        if let Err(message) = params.grant.validate() {
            return self.error(id, "INVALID_PARAMS", message, None);
        }
        let pointer = match self.pointer_for_request(&params.grant) {
            Ok((pointer, _)) => pointer,
            Err(error) => return self.pointer_request_error(id, error),
        };
        let result = if down {
            pointer.mouse_down(&params.button)
        } else {
            pointer.mouse_up(&params.button)
        };
        match result {
            Ok(()) => self.success(
                id,
                json!({"held": pointer.held(), "backend": "linux.uinput.native"}),
                None,
            ),
            Err(error) => self.pointer_operation_error(id, error),
        }
    }

    fn pointer_held(&self, id: String) -> DispatchOutcome {
        self.success(
            id,
            json!({
                "held": self.pointer.as_ref().map_or_else(
                    Vec::new,
                    |(_, _, pointer)| pointer.held(),
                ),
                "backend": "linux.uinput.native",
            }),
            None,
        )
    }

    fn pointer_release_all(&self, id: String) -> DispatchOutcome {
        let Some((_, _, pointer)) = self.pointer.as_ref() else {
            return self.success(
                id,
                json!({"released": [], "backend": "linux.uinput.native"}),
                None,
            );
        };
        match pointer.release_all() {
            Ok(released) => self.success(
                id,
                json!({"released": released, "backend": "linux.uinput.native"}),
                None,
            ),
            Err(error) => self.pointer_operation_error(id, error),
        }
    }

    fn pointer_take_auto_released(&self, id: String) -> DispatchOutcome {
        self.success(
            id,
            json!({
                "released": self.pointer.as_ref().map_or_else(
                    Vec::new,
                    |(_, _, pointer)| pointer.take_auto_released(),
                ),
                "backend": "linux.uinput.native",
            }),
            None,
        )
    }

    fn pointer_position(&self, id: String) -> DispatchOutcome {
        self.success(
            id,
            json!({
                "position": self.pointer.as_ref().and_then(
                    |(_, _, pointer)| pointer.position(),
                ),
                "backend": "linux.uinput.native",
            }),
            None,
        )
    }

    fn pointer_for_request(
        &mut self,
        params: &PointerGrantParams,
    ) -> Result<(Arc<dyn PointerService>, f64), PointerRequestError> {
        self.validate_pointer_grant(params)
            .map_err(PointerRequestError::Lifecycle)?;
        let geometry = self.pointer_geometry(&params.topology_id)?;
        let (pointer, waited_seconds) =
            self.ensure_pointer(&params.topology_id, geometry, params.config())?;
        if let Err(failure) = self.validate_pointer_grant(params) {
            self.close_pointer();
            return Err(PointerRequestError::Lifecycle(failure));
        }
        if let Err(error) = self.pointer_geometry(&params.topology_id) {
            self.close_pointer();
            return Err(error);
        }
        Ok((pointer, waited_seconds))
    }

    fn validate_pointer_grant(&self, params: &PointerGrantParams) -> Result<(), LifecycleFailure> {
        let lifecycle = self
            .lifecycle
            .as_ref()
            .expect("initialized dispatcher has a lifecycle");
        if !lifecycle.matches_token(&params.grant_id, params.revoke_epoch) {
            return Err(LifecycleFailure::Revoked);
        }
        lifecycle.validate_now()
    }

    fn pointer_geometry(
        &mut self,
        expected_topology: &str,
    ) -> Result<PointerGeometry, PointerRequestError> {
        #[cfg(feature = "test-harness")]
        if matches!(self.mode, BackendMode::DeterministicTest) {
            let (width, height) = match expected_topology {
                "test-layout" => (3840, 1080),
                "test-layout-wide" => (5120, 1440),
                _ => return Err(PointerRequestError::DisplayChanged),
            };
            return PointerGeometry::new(width, height).map_err(PointerRequestError::Geometry);
        }

        let snapshot = self
            .current_display_snapshot()
            .map_err(PointerRequestError::DisplayUnknown)?;
        if snapshot.topology_id != expected_topology {
            return Err(PointerRequestError::DisplayChanged);
        }
        let (width, height) = pcbridge_core::display::canvas_size(&snapshot.monitors);
        PointerGeometry::new(width, height).map_err(PointerRequestError::Geometry)
    }

    fn ensure_pointer(
        &mut self,
        topology_id: &str,
        geometry: PointerGeometry,
        config: PointerConfig,
    ) -> Result<(Arc<dyn PointerService>, f64), PointerRequestError> {
        if self
            .pointer
            .as_ref()
            .is_some_and(|(_, _, pointer)| pointer.is_closed())
        {
            self.pointer.take();
        }
        if self
            .pointer
            .as_ref()
            .is_some_and(|(open_topology, _, _)| open_topology != topology_id)
        {
            self.close_pointer();
        }
        if let Some((_, configured, pointer)) = self.pointer.as_ref() {
            if *configured != config {
                return Err(PointerRequestError::SettingsChanged);
            }
            return Ok((Arc::clone(pointer), 0.0));
        }

        let state_file = self
            .state_dir
            .as_ref()
            .expect("initialized dispatcher has a state directory")
            .join("pointer.json");
        let (pointer, settle): (Arc<dyn PointerService>, Duration) = match &self.mode {
            BackendMode::Production { .. } => {
                let device =
                    EvdevPointerDevice::create(geometry).map_err(PointerRequestError::Device)?;
                (
                    Arc::new(NativePointer::new(
                        device,
                        SystemPointerClock::default(),
                        geometry,
                        config,
                        Some(state_file),
                    )),
                    DEVICE_SETTLE,
                )
            }
            #[cfg(feature = "test-harness")]
            BackendMode::DeterministicTest => (
                Arc::new(Pointer::new(
                    NullPointerDevice,
                    SystemPointerClock::default(),
                    geometry,
                    config,
                    Some(state_file),
                )),
                Duration::ZERO,
            ),
        };
        let lifecycle = self
            .lifecycle
            .as_ref()
            .expect("initialized dispatcher has a lifecycle");
        lifecycle.register_fail_closed(Arc::new(PointerFailClosed(Arc::downgrade(&pointer))));
        self.pointer = Some((topology_id.to_owned(), config, Arc::clone(&pointer)));
        std::thread::sleep(settle);
        Ok((pointer, settle.as_secs_f64()))
    }

    fn pointer_request_error(&self, id: String, error: PointerRequestError) -> DispatchOutcome {
        match error {
            PointerRequestError::Lifecycle(failure) => self.keyboard_lifecycle_error(id, failure),
            PointerRequestError::DisplayChanged => self.typed_error(
                id,
                ErrorBody {
                    code: "DISPLAY_CHANGED".to_owned(),
                    message: "the pointer topology no longer matches the resolved coordinate"
                        .to_owned(),
                    retryable: true,
                    category: "coordinate".to_owned(),
                },
            ),
            PointerRequestError::DisplayUnknown(message) => self.typed_error(
                id,
                ErrorBody {
                    code: "DISPLAY_MAPPING_UNKNOWN".to_owned(),
                    message,
                    retryable: true,
                    category: "coordinate".to_owned(),
                },
            ),
            PointerRequestError::SettingsChanged => self.error(
                id,
                "INVALID_PARAMS",
                "pointer settings cannot change while the native pointer is open",
                None,
            ),
            PointerRequestError::Geometry(error) => {
                self.error(id, "INVALID_PARAMS", error.to_string(), None)
            }
            PointerRequestError::Device(error) => {
                let (code, category, retryable) = match error.kind() {
                    std::io::ErrorKind::NotFound => ("DEPENDENCY_MISSING", "capability", false),
                    std::io::ErrorKind::PermissionDenied => {
                        ("DEVICE_NOT_GRANTED", "permission", true)
                    }
                    _ => ("BACKEND_UNAVAILABLE", "capability", true),
                };
                self.typed_error(
                    id,
                    ErrorBody {
                        code: code.to_owned(),
                        message: format!("native pointer could not open /dev/uinput: {error}"),
                        retryable,
                        category: category.to_owned(),
                    },
                )
            }
        }
    }

    fn pointer_operation_error(&self, id: String, error: PointerError) -> DispatchOutcome {
        match error {
            PointerError::InvalidGeometry
            | PointerError::UnknownButton(_)
            | PointerError::InvalidClickCount => {
                self.error(id, "INVALID_PARAMS", error.to_string(), None)
            }
            PointerError::Closed => self.typed_error(
                id,
                ErrorBody {
                    code: "BACKEND_UNAVAILABLE".to_owned(),
                    message: error.to_string(),
                    retryable: true,
                    category: "capability".to_owned(),
                },
            ),
            PointerError::Device(source) => self.typed_error(
                id,
                ErrorBody {
                    code: "EXECUTION_UNKNOWN".to_owned(),
                    message: format!("native pointer write failed: {source}"),
                    retryable: false,
                    category: "execution".to_owned(),
                },
            ),
        }
    }

    fn close_pointer(&mut self) {
        if let Some((_, _, pointer)) = self.pointer.take() {
            let _ = pointer.close();
        }
    }

    fn native_capture_error(&self, id: String, error: NativeCaptureError) -> DispatchOutcome {
        let (code, category, retryable) = match &error {
            NativeCaptureError::DisplayChanged => ("DISPLAY_CHANGED", "capture", true),
            NativeCaptureError::DisplayUnknown(_) => ("DISPLAY_MAPPING_UNKNOWN", "capture", true),
            NativeCaptureError::Session(SessionFailure::Guard(failure))
            | NativeCaptureError::Capture(CaptureError::Guard(failure)) => {
                (failure.code(), "safety", true)
            }
            NativeCaptureError::Session(SessionFailure::Busy(_)) => ("BUSY", "execution", true),
            NativeCaptureError::Capture(CaptureError::Timeout(_)) => {
                ("FRAME_TIMEOUT", "capture", true)
            }
            NativeCaptureError::Capture(CaptureError::Canceled) => ("CANCELLED", "execution", true),
            NativeCaptureError::Capture(CaptureError::UnsupportedFormat(_)) => {
                ("FRAME_FORMAT_UNSUPPORTED", "capture", false)
            }
            NativeCaptureError::Capture(CaptureError::Frame(
                pcbridge_core::frame::FrameError::TooManyPixels { .. }
                | pcbridge_core::frame::FrameError::PayloadTooLarge { .. },
            )) => ("FRAME_TOO_LARGE", "capture", false),
            NativeCaptureError::Capture(CaptureError::Frame(_)) => {
                ("INVALID_FRAME", "capture", false)
            }
            NativeCaptureError::Session(_)
            | NativeCaptureError::Capture(CaptureError::Unavailable(_))
            | NativeCaptureError::Capture(CaptureError::Stream(_)) => {
                ("BACKEND_UNAVAILABLE", "capability", true)
            }
        };
        self.typed_error(
            id,
            ErrorBody {
                code: code.to_owned(),
                message: error.to_string(),
                retryable,
                category: category.to_owned(),
            },
        )
    }
}

impl Drop for Dispatcher {
    fn drop(&mut self) {
        self.close_keyboard();
        self.close_pointer();
    }
}

#[derive(Debug, Deserialize)]
struct InitializeParams {
    client_version: String,
    supported_minor: Vec<u16>,
    state_dir: String,
    runtime_dir: String,
}

#[derive(Debug, Deserialize)]
struct CancelParams {
    target_id: String,
}

#[derive(Debug, Deserialize)]
struct CaptureFrameParams {
    display_id: String,
    topology_id: String,
    session_id: String,
    grant_id: String,
    revoke_epoch: u64,
    timeout_ms: u64,
    freshness: String,
    #[serde(default = "default_true")]
    include_pointer: bool,
}

#[derive(Debug, Deserialize)]
struct SessionOpenParams {
    topology_id: String,
    session_id: String,
    grant_id: String,
    revoke_epoch: u64,
    #[serde(default = "default_true")]
    include_pointer: bool,
}

#[derive(Debug, Deserialize)]
struct KeyboardGrantParams {
    grant_id: String,
    revoke_epoch: u64,
    hold_max_seconds: u64,
}

#[derive(Debug, Deserialize)]
struct KeyboardActionParams {
    #[serde(flatten)]
    grant: KeyboardGrantParams,
    combo: String,
}

#[derive(Debug, Deserialize)]
struct PointerGrantParams {
    grant_id: String,
    revoke_epoch: u64,
    hold_max_seconds: u64,
    topology_id: String,
    pointer_speed: f64,
    pointer_max_ms: f64,
}

#[derive(Debug, Deserialize)]
struct PointerMoveParams {
    #[serde(flatten)]
    grant: PointerGrantParams,
    x: i32,
    y: i32,
    #[serde(default)]
    smooth: Option<bool>,
}

#[derive(Debug, Deserialize)]
struct PointerClickParams {
    #[serde(flatten)]
    grant: PointerGrantParams,
    button: String,
    count: u8,
}

#[derive(Debug, Deserialize)]
struct PointerDragParams {
    #[serde(flatten)]
    grant: PointerGrantParams,
    x1: i32,
    y1: i32,
    x2: i32,
    y2: i32,
    button: String,
}

#[derive(Debug, Deserialize)]
struct PointerScrollParams {
    #[serde(flatten)]
    grant: PointerGrantParams,
    amount: i32,
    #[serde(default)]
    horizontal: bool,
}

#[derive(Debug, Deserialize)]
struct PointerButtonParams {
    #[serde(flatten)]
    grant: PointerGrantParams,
    button: String,
}

/// A dump gives up after this long unless the request asks for less; the
/// host's own request timeout is longer, so the reason arrives as TIMEOUT.
const DUMP_DEADLINE_MS: u64 = 15_000;
const DUMP_DEADLINE_MS_MAX: u64 = 20_000;
/// The largest list one dump may return (the walk visits 25 times more).
const MAX_DUMP_NODES: u64 = 2_000;

fn accessibility_error_body(error: &AccessibilityError) -> ErrorBody {
    let (category, retryable) = match error.code {
        "ELEMENT_STALE" | "ELEMENT_AMBIGUOUS" | "TARGET_MISMATCH" => ("accessibility", true),
        "TIMEOUT" => ("execution", true),
        _ => ("capability", true),
    };
    ErrorBody {
        code: error.code.to_owned(),
        message: error.message.clone(),
        retryable,
        category: category.to_owned(),
    }
}

#[cfg(feature = "test-harness")]
fn load_fixture_desktop(desktop: &str) -> Result<FixtureTree, ErrorBody> {
    let unsupported = |message: String| ErrorBody {
        code: "UNSUPPORTED".to_owned(),
        message,
        retryable: false,
        category: "capability".to_owned(),
    };
    let path = std::env::var_os("PCBRIDGE_TEST_A11Y_FIXTURE")
        .ok_or_else(|| unsupported("the test harness has no accessibility fixture".to_owned()))?;
    let text = std::fs::read_to_string(&path).map_err(|error| {
        unsupported(format!("the accessibility fixture is unreadable: {error}"))
    })?;
    let fixture: Value = serde_json::from_str(&text)
        .map_err(|error| unsupported(format!("the accessibility fixture is not JSON: {error}")))?;
    FixtureTree::from_fixture(&fixture, desktop).map_err(unsupported)
}

trait AccessibilityGrant {
    fn grant_id(&self) -> &str;
    fn revoke_epoch(&self) -> u64;
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct AccessibilityGrantParams {
    grant_id: String,
    revoke_epoch: u64,
}

fn default_target() -> String {
    "focused".to_owned()
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct AccessibilityDumpParams {
    grant_id: String,
    revoke_epoch: u64,
    #[serde(default = "default_target")]
    target: String,
    #[serde(default = "default_true")]
    interactive_only: bool,
    /// 0 means the default, as in the Python helper.
    #[serde(default)]
    max_nodes: u64,
    /// 0 means the default.
    #[serde(default)]
    deadline_ms: u64,
}

impl AccessibilityGrant for AccessibilityGrantParams {
    fn grant_id(&self) -> &str {
        &self.grant_id
    }

    fn revoke_epoch(&self) -> u64 {
        self.revoke_epoch
    }
}

impl AccessibilityGrant for AccessibilityDumpParams {
    fn grant_id(&self) -> &str {
        &self.grant_id
    }

    fn revoke_epoch(&self) -> u64 {
        self.revoke_epoch
    }
}

const CLIPBOARD_BACKEND: &str = "linux.wl-clipboard.native";

trait ClipboardGrant {
    fn grant_id(&self) -> &str;
    fn revoke_epoch(&self) -> u64;
    fn validate(&self) -> Result<(), &'static str>;
}

/// Clipboard requests name the grant and nothing else: the content is the
/// binary payload, never a header field that could reach a log.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ClipboardGrantParams {
    grant_id: String,
    revoke_epoch: u64,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ClipboardWriteParams {
    grant_id: String,
    revoke_epoch: u64,
    mime: String,
}

fn validate_grant_id(grant_id: &str) -> Result<(), &'static str> {
    const MAX_ID_BYTES: usize = 256;
    if grant_id.is_empty() || grant_id.len() > MAX_ID_BYTES {
        return Err("grant_id must be nonempty and bounded");
    }
    Ok(())
}

impl ClipboardGrant for ClipboardGrantParams {
    fn grant_id(&self) -> &str {
        &self.grant_id
    }

    fn revoke_epoch(&self) -> u64 {
        self.revoke_epoch
    }

    fn validate(&self) -> Result<(), &'static str> {
        validate_grant_id(&self.grant_id)
    }
}

impl ClipboardGrant for ClipboardWriteParams {
    fn grant_id(&self) -> &str {
        &self.grant_id
    }

    fn revoke_epoch(&self) -> u64 {
        self.revoke_epoch
    }

    fn validate(&self) -> Result<(), &'static str> {
        validate_grant_id(&self.grant_id)?;
        clipboard::validate_mime(&self.mime)
            .map_err(|_| "mime must be 1 to 256 bytes without control characters")
    }
}

impl KeyboardGrantParams {
    fn validate(&self) -> Result<(), &'static str> {
        const MAX_ID_BYTES: usize = 256;
        if self.grant_id.is_empty() || self.grant_id.len() > MAX_ID_BYTES {
            return Err("grant_id must be nonempty and bounded");
        }
        if self.hold_max_seconds != 0 && !(5..=3_600).contains(&self.hold_max_seconds) {
            return Err("hold_max_seconds must be zero or between 5 and 3600");
        }
        Ok(())
    }
}

impl KeyboardActionParams {
    fn validate(&self) -> Result<(), &'static str> {
        self.grant.validate()?;
        if self.combo.is_empty() || self.combo.len() > 256 {
            return Err("combo must be nonempty and at most 256 bytes");
        }
        Ok(())
    }
}

impl PointerGrantParams {
    fn validate(&self) -> Result<(), &'static str> {
        const MAX_ID_BYTES: usize = 256;
        const MAX_TOPOLOGY_BYTES: usize = 16 * 1024;
        if self.grant_id.is_empty() || self.grant_id.len() > MAX_ID_BYTES {
            return Err("grant_id must be nonempty and bounded");
        }
        if self.topology_id.is_empty() || self.topology_id.len() > MAX_TOPOLOGY_BYTES {
            return Err("topology_id must be nonempty and bounded");
        }
        if self.hold_max_seconds != 0 && !(5..=3_600).contains(&self.hold_max_seconds) {
            return Err("hold_max_seconds must be zero or between 5 and 3600");
        }
        if !self.pointer_speed.is_finite()
            || (self.pointer_speed != 0.0 && !(200.0..=100_000.0).contains(&self.pointer_speed))
        {
            return Err("pointer_speed must be zero or between 200 and 100000");
        }
        if !self.pointer_max_ms.is_finite() || !(20.0..=5_000.0).contains(&self.pointer_max_ms) {
            return Err("pointer_max_ms must be between 20 and 5000");
        }
        Ok(())
    }

    fn config(&self) -> PointerConfig {
        PointerConfig {
            speed: self.pointer_speed,
            max_ms: self.pointer_max_ms,
            step: Duration::from_millis(8),
            hold_max: Duration::from_secs(self.hold_max_seconds),
            drag_min_steps: 10,
        }
    }
}

const POINTER_GRANT_FIELDS: &[&str] = &[
    "grant_id",
    "revoke_epoch",
    "hold_max_seconds",
    "topology_id",
    "pointer_speed",
    "pointer_max_ms",
];

fn parse_pointer_params<T: DeserializeOwned>(
    params: Value,
    action_fields: &[&str],
) -> Result<T, String> {
    let object = params
        .as_object()
        .ok_or_else(|| "pointer parameters must be an object".to_owned())?;
    if let Some(field) = object.keys().find(|field| {
        !POINTER_GRANT_FIELDS.contains(&field.as_str()) && !action_fields.contains(&field.as_str())
    }) {
        return Err(format!("unknown pointer parameter '{field}'"));
    }
    serde_json::from_value(params).map_err(|error| error.to_string())
}

impl SessionOpenParams {
    fn validate(&self) -> Result<(), &'static str> {
        const MAX_ID_BYTES: usize = 256;
        const MAX_TOPOLOGY_BYTES: usize = 16 * 1024;

        if self.topology_id.is_empty() || self.topology_id.len() > MAX_TOPOLOGY_BYTES {
            return Err("topology_id must be nonempty and bounded");
        }
        if self.session_id.is_empty() || self.session_id.len() > MAX_ID_BYTES {
            return Err("session_id must be nonempty and bounded");
        }
        if self.grant_id.is_empty() || self.grant_id.len() > MAX_ID_BYTES {
            return Err("grant_id must be nonempty and bounded");
        }
        Ok(())
    }
}

const fn default_true() -> bool {
    true
}

impl CaptureFrameParams {
    fn validate(&self) -> Result<(), &'static str> {
        const MAX_ID_BYTES: usize = 256;
        const MAX_TOPOLOGY_BYTES: usize = 16 * 1024;

        let display = self
            .display_id
            .split_once(':')
            .filter(|(scheme, target)| !scheme.is_empty() && !target.is_empty());
        if display.is_none() || self.display_id.len() > MAX_ID_BYTES {
            return Err("display_id must be a scoped, nonempty identifier");
        }
        if self.topology_id.is_empty() || self.topology_id.len() > MAX_TOPOLOGY_BYTES {
            return Err("topology_id must be nonempty and bounded");
        }
        if self.session_id.is_empty() || self.session_id.len() > MAX_ID_BYTES {
            return Err("session_id must be nonempty and bounded");
        }
        if self.grant_id.is_empty() || self.grant_id.len() > MAX_ID_BYTES {
            return Err("grant_id must be nonempty and bounded");
        }
        if !(1..=8_000).contains(&self.timeout_ms) {
            return Err("timeout_ms must be between 1 and 8000");
        }
        if self.freshness != "after_request" {
            return Err("freshness must be 'after_request'");
        }
        Ok(())
    }
}

fn parse_cancel(params: Value) -> Result<String, String> {
    let params: CancelParams = serde_json::from_value(params)
        .map_err(|error| format!("invalid cancel parameters: {error}"))?;
    if params.target_id.is_empty() {
        return Err("target_id must be nonempty".to_owned());
    }
    Ok(params.target_id)
}

fn ping_result(params: Value) -> Value {
    match params.get("nonce") {
        Some(nonce) => json!({"pong": true, "nonce": nonce}),
        None => json!({"pong": true}),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn production_mode_never_advertises_fake_backend() {
        let mode = BackendMode::production();
        let capabilities = mode.capabilities();
        assert_eq!(capabilities["backend"], "linux.mutter.pipewire");
        assert_eq!(
            mode.features(),
            vec![
                "display.snapshot",
                "capture.on_demand",
                "capture.session_open",
                "input.keyboard",
                "input.pointer",
                "clipboard",
                "accessibility.read"
            ]
        );
        let monitor = &capabilities["capabilities"][0];
        assert_eq!(monitor["name"], "capture.monitor");
        // A runtime probe now: `supported` needs Mutter and PipeWire on this
        // machine, and anything else has to say why.
        match monitor["status"].as_str() {
            Some("supported") => assert!(monitor.get("reason").is_none()),
            Some("unavailable") => assert!(
                monitor["reason"]
                    .as_str()
                    .is_some_and(|reason| !reason.is_empty())
            ),
            other => panic!("unexpected capture.monitor status {other:?}"),
        }
        let keyboard = &capabilities["capabilities"][1];
        assert_eq!(keyboard["name"], "input.keyboard");
        match keyboard["status"].as_str() {
            Some("degraded") => assert!(
                keyboard["reason"]
                    .as_str()
                    .is_some_and(|reason| !reason.is_empty())
            ),
            Some("unavailable") => {
                assert_eq!(keyboard["reason_code"], "DEPENDENCY_MISSING")
            }
            other => panic!("unexpected input.keyboard status {other:?}"),
        }
        let pointer = &capabilities["capabilities"][2];
        assert_eq!(pointer["name"], "input.pointer");
        match pointer["status"].as_str() {
            Some("degraded") => assert!(
                pointer["reason"]
                    .as_str()
                    .is_some_and(|reason| !reason.is_empty())
            ),
            Some("unavailable") => {
                assert_eq!(pointer["reason_code"], "DEPENDENCY_MISSING")
            }
            other => panic!("unexpected input.pointer status {other:?}"),
        }
        // Probed without running either program: a capability request must
        // not read the user's clipboard.
        for (index, name) in [(3, "clipboard.read"), (4, "clipboard.write")] {
            let entry = &capabilities["capabilities"][index];
            assert_eq!(entry["name"], name);
            assert_eq!(entry["permission_scope"], "os.clipboard");
            match entry["status"].as_str() {
                Some("supported") => assert_eq!(
                    entry["limitations"],
                    if name == "clipboard.write" {
                        json!([readiness::SINGLE_MIME_LIMITATION])
                    } else {
                        json!([])
                    }
                ),
                Some("unavailable") => assert!(
                    entry["reason"]
                        .as_str()
                        .is_some_and(|reason| !reason.is_empty())
                ),
                other => panic!("unexpected {name} status {other:?}"),
            }
        }
        // Asked of the bus daemon only: no application tree is read.
        for (index, name) in [(5, "accessibility.read"), (6, "window.list")] {
            let entry = &capabilities["capabilities"][index];
            assert_eq!(entry["name"], name);
            assert_eq!(entry["permission_scope"], "os.accessibility");
            match entry["status"].as_str() {
                Some("supported") => assert_eq!(name, "accessibility.read"),
                Some("degraded") => assert_eq!(
                    entry["limitations"],
                    json!([readiness::WINDOW_LIST_LIMITATION])
                ),
                Some("unavailable") => assert!(
                    entry["reason"]
                        .as_str()
                        .is_some_and(|reason| !reason.is_empty())
                ),
                other => panic!("unexpected {name} status {other:?}"),
            }
        }
    }
}
