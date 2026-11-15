//! The protocol dispatcher on Windows (docs/native/protocol-v1.md).
//!
//! The same requests and answers as the Linux dispatcher, served by Win32
//! instead of AT-SPI, PipeWire, uinput and X11. It grows with the Windows
//! port's W6 stages; a method whose backend has not arrived yet answers
//! `BACKEND_UNAVAILABLE`, which the Python side reports as an unavailable
//! capability rather than a failure.

use std::path::{Path, PathBuf};
use std::sync::{Arc, Weak};
use std::time::Duration;

use pcbridge_core::{
    ErrorBody, Frame, PROTOCOL_MAJOR, PROTOCOL_MINOR, ProtocolError, RequestHeader, ResponseHeader,
};
use serde::Deserialize;
use serde_json::{Value, json};

use crate::fail_closed::FailClosed;
use crate::input::requests::{
    KeyboardActionParams, KeyboardGrantParams, POINTER_STEP, PointerButtonParams,
    PointerClickParams, PointerDragParams, PointerEnsureParams, PointerGrantParams,
    PointerMoveParams, PointerScrollParams, RelativeGrantParams, RelativeMoveParams,
    parse_pointer_params, parse_relative_params,
};
use crate::input::{
    DEFAULT_CLICK_HOLD, Keyboard, KeyboardError, KeyboardService, MAX_CLICK_HOLD_MS, Pointer,
    PointerConfig, PointerError, PointerGeometry, PointerService, Relative, RelativeService,
    SystemClock, SystemPointerClock,
};
use crate::platform::windows::clipboard::{self, ClipboardError};
use crate::platform::windows::input::{
    self as win_input, SendInputKeyboard, SendInputPointer, SendInputRelative,
};
use crate::platform::windows::lifecycle::{Binding, Failure};
use crate::platform::windows::{capture, display};

/// The most text one `input.keyboard.type` request may carry. At one
/// character per 10 ms it types in 80 s, under the 110 s call limit.
const MAX_TYPE_CHARS: usize = 8_000;

pub const DISPLAY_BACKEND: &str = "windows.gdi";
/// A monitor's id on the wire: the scheme and the GDI device name.
pub const DISPLAY_SCHEME: &str = "windows:";

#[derive(Debug)]
pub enum BackendMode {
    Production { instance_id: String },
}

impl BackendMode {
    #[must_use]
    pub fn production() -> Self {
        Self::Production {
            instance_id: format!("native-{}", std::process::id()),
        }
    }

    fn instance_id(&self) -> &str {
        match self {
            Self::Production { instance_id } => instance_id,
        }
    }

    fn features(&self) -> Vec<&'static str> {
        vec![
            "display.snapshot",
            "capture.on_demand",
            "input.keyboard",
            "input.keyboard.type",
            "input.pointer",
            "input.pointer_relative",
            "clipboard",
        ]
    }

    fn capabilities(&self) -> Value {
        json!({
            "backend": DISPLAY_BACKEND,
            "capabilities": [
                json!({
                    "name": "capture.monitor",
                    "status": "supported",
                    "permission_scope": "os.capture",
                    "backend": capture::BACKEND,
                }),
                supported("input.keyboard", "os.keyboard", win_input::BACKEND),
                supported("input.pointer", "os.pointer", win_input::BACKEND),
                supported("input.pointer_relative", "os.pointer", win_input::BACKEND),
                supported("clipboard.read", "os.clipboard", clipboard::BACKEND),
                supported("clipboard.write", "os.clipboard", clipboard::BACKEND),
                unavailable("accessibility.read", "os.accessibility"),
                unavailable("accessibility.windows", "os.accessibility"),
                unavailable("accessibility.action", "os.accessibility"),
            ],
        })
    }
}

fn supported(name: &str, scope: &str, backend: &str) -> Value {
    json!({
        "name": name,
        "status": "supported",
        "permission_scope": scope,
        "backend": backend,
    })
}

fn unavailable(name: &str, scope: &str) -> Value {
    json!({
        "name": name,
        "status": "unavailable",
        "permission_scope": scope,
        "reason_code": "BACKEND_UNAVAILABLE",
        "reason": "this Windows backend is not built yet",
    })
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

#[derive(Debug, Deserialize)]
struct InitializeParams {
    client_version: String,
    supported_minor: Vec<u16>,
    state_dir: String,
    runtime_dir: String,
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
    #[serde(default)]
    include_pointer: bool,
}

#[derive(Debug, Deserialize)]
struct CancelParams {
    target_id: String,
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

#[derive(Clone, Copy)]
enum KeyAction {
    Key,
    Down,
    Up,
}

enum PointerRequestError {
    Lifecycle(Failure),
    DisplayChanged,
    DisplayUnknown(String),
    SettingsChanged,
    Geometry(PointerError),
}

type OpenPointer = (String, PointerConfig, Arc<dyn PointerService>);

pub struct Dispatcher {
    mode: BackendMode,
    initialized: bool,
    negotiated_minor: Option<u16>,
    state_dir: Option<PathBuf>,
    binding: Option<Binding>,
    frame_sequence: u64,
    keyboard: Option<(u64, Arc<dyn KeyboardService>)>,
    pointer: Option<OpenPointer>,
    relative: Option<Arc<dyn RelativeService>>,
}

impl Dispatcher {
    #[must_use]
    pub fn new(mode: BackendMode) -> Self {
        Self {
            mode,
            initialized: false,
            negotiated_minor: None,
            state_dir: None,
            binding: None,
            frame_sequence: 0,
            keyboard: None,
            pointer: None,
            relative: None,
        }
    }

    pub fn dispatch(&mut self, frame: Frame) -> Result<DispatchOutcome, ProtocolError> {
        let request = RequestHeader::from_value(frame.header)?;
        if request.id.is_empty() || request.method.is_empty() {
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
        if !frame.binary.is_empty()
            && request.method != "clipboard.write"
            && request.method != "input.keyboard.type"
            && request.method != "accessibility.set_text"
        {
            return Ok(self.error(
                request.id,
                "UNEXPECTED_BINARY",
                "control methods do not accept a binary payload",
                None,
            ));
        }
        let binary = frame.binary;
        let (id, params) = (request.id, request.params);
        let outcome = match request.method.as_str() {
            "input.keyboard.ensure" => self.keyboard_ensure(id, params),
            "input.keyboard.key" => self.keyboard_action(id, params, KeyAction::Key),
            "input.keyboard.key_down" => self.keyboard_action(id, params, KeyAction::Down),
            "input.keyboard.key_up" => self.keyboard_action(id, params, KeyAction::Up),
            "input.keyboard.type" => self.keyboard_type(id, params, &binary),
            "input.keyboard.held" => self.keyboard_held(id),
            "input.keyboard.release_all" => self.keyboard_release_all(id),
            "input.keyboard.take_auto_released" => self.keyboard_take_auto_released(id),
            "input.pointer.ensure" => self.pointer_ensure(id, params),
            "input.pointer.move" => self.pointer_move(id, params),
            "input.pointer.move_by" => self.pointer_move_by(id, params),
            "input.pointer.click" => self.pointer_click(id, params),
            "input.pointer.drag" => self.pointer_drag(id, params),
            "input.pointer.scroll" => self.pointer_scroll(id, params),
            "input.pointer.mouse_down" => self.pointer_button(id, params, true),
            "input.pointer.mouse_up" => self.pointer_button(id, params, false),
            "input.pointer.held" => self.pointer_held(id),
            "input.pointer.release_all" => self.pointer_release_all(id),
            "input.pointer.take_auto_released" => self.pointer_take_auto_released(id),
            "input.pointer.position" => self.pointer_position(id),
            "clipboard.read" => self.clipboard_read(id, params),
            "clipboard.write" => self.clipboard_write(id, params, &binary),
            "clipboard.clear" => self.clipboard_clear(id, params),
            "ping" => self.success(id, ping_result(params), None),
            "capabilities" => self.success(id, self.mode.capabilities(), None),
            "display.snapshot" => self.display_snapshot(id),
            "capture.frame" => self.capture_frame(id, params),
            "cancel" => match serde_json::from_value::<CancelParams>(params) {
                Ok(params) if !params.target_id.is_empty() => self.success(
                    id,
                    json!({"target_id": params.target_id, "canceled": false}),
                    None,
                ),
                _ => self.error(id, "INVALID_PARAMS", "invalid cancel parameters", None),
            },
            "shutdown" => self.success(
                id,
                json!({"shutdown": true}),
                Some(CloseConnection::Shutdown),
            ),
            method if is_known(method) => self.error(
                id,
                "BACKEND_UNAVAILABLE",
                format!("'{method}' has no Windows backend yet"),
                None,
            ),
            method => self.error(
                id,
                "UNKNOWN_METHOD",
                format!("method '{method}' is not available"),
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
        display::ensure_dpi_awareness();
        let binding = Binding::bind(Path::new(&params.state_dir));
        let lease_bound = binding.is_bound();
        self.binding = Some(binding);
        self.state_dir = Some(PathBuf::from(&params.state_dir));
        self.initialized = true;
        self.negotiated_minor = Some(PROTOCOL_MINOR);
        self.success(
            request.id,
            json!({
                "instance_id": self.mode.instance_id(),
                "native_version": crate::build_info::VERSION,
                "build_id": crate::build_info::build_id(),
                "platform": std::env::consts::OS,
                "features": self.mode.features(),
                "lease_bound": lease_bound,
            }),
            None,
        )
    }

    /// Read-only display metadata, outside the grant as on Linux.
    fn display_snapshot(&mut self, id: String) -> DispatchOutcome {
        match display::monitors() {
            Ok(monitors) => {
                let (width, height) = pcbridge_core::display::canvas_size(&monitors);
                let topology_id = pcbridge_core::display::topology_id(&monitors);
                let rows: Vec<Value> = monitors
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
                            "pixel_ratio": monitor.pixel_ratio(),
                            "primary": monitor.primary,
                            "primary_is_focus": monitor.primary_is_focus,
                            "name": monitor.name,
                            "transform": monitor.transform,
                            "serial": monitor.serial,
                        })
                    })
                    .collect();
                self.success(
                    id,
                    json!({"topology_id": topology_id, "canvas": [width, height], "monitors": rows}),
                    None,
                )
            }
            Err(error) => self.error(id, "DISPLAY_MAPPING_UNKNOWN", error, None),
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
        if params.freshness != "after_request" {
            return self.error(
                id,
                "INVALID_PARAMS",
                "only freshness \"after_request\" is accepted",
                None,
            );
        }
        let bounded = |text: &str, limit: usize| !text.is_empty() && text.len() <= limit;
        if !bounded(&params.display_id, 256)
            || !bounded(&params.topology_id, 16 * 1024)
            || !bounded(&params.session_id, 256)
            || !bounded(&params.grant_id, 256)
        {
            return self.error(
                id,
                "INVALID_PARAMS",
                "capture identifiers are empty or too long",
                None,
            );
        }
        let Some(device) = params.display_id.strip_prefix(DISPLAY_SCHEME) else {
            return self.error(
                id,
                "INVALID_PARAMS",
                format!("display_id must be '{DISPLAY_SCHEME}<device>' on Windows"),
                None,
            );
        };
        if let Err(failure) = self
            .binding
            .as_ref()
            .expect("an initialized dispatcher is bound")
            .validate(&params.grant_id, params.revoke_epoch)
        {
            return self.typed_error(id, failure.code(), failure.message(), "safety");
        }
        let monitors = match display::monitors() {
            Ok(monitors) => monitors,
            Err(error) => {
                return self.typed_error(id, "DISPLAY_MAPPING_UNKNOWN", &error, "capture");
            }
        };
        if pcbridge_core::display::topology_id(&monitors) != params.topology_id {
            return self.typed_error(
                id,
                "DISPLAY_CHANGED",
                "the monitor layout changed",
                "capture",
            );
        }
        let Some(monitor) = monitors.iter().find(|monitor| monitor.connector == device) else {
            return self.typed_error(
                id,
                "DISPLAY_MAPPING_UNKNOWN",
                "no monitor with that id",
                "capture",
            );
        };
        let timeout = std::time::Duration::from_millis(params.timeout_ms.clamp(1, 8000));
        let captured = match capture::capture(device, timeout) {
            Ok(captured) => captured,
            Err(error) => return self.typed_error(id, "CAPTURE_FAILED", &error, "capture"),
        };
        let encode_started = std::time::Instant::now();
        let png = match captured.frame.to_png() {
            Ok(png) => png,
            Err(error) => {
                return self.typed_error(id, "CAPTURE_FAILED", &error.to_string(), "capture");
            }
        };
        self.frame_sequence += 1;
        let result = json!({
            "display_id": params.display_id,
            "topology_id": params.topology_id,
            "session_id": params.session_id,
            "frame_sequence": self.frame_sequence,
            "frame_timestamp_ns": 0,
            "frame_identity_source": "request_counter",
            "pixel_size": [captured.frame.width, captured.frame.height],
            "desktop_rect": [monitor.x, monitor.y, monitor.width, monitor.height],
            "stale_frames": 0,
            // Duplication leaves the pointer out; drawing it is still to come.
            "include_pointer": false,
            "pointer_requested": params.include_pointer,
            "revoke_epoch": params.revoke_epoch,
            "wait_ms": captured.waited.as_secs_f64() * 1000.0,
            "encode_ms": encode_started.elapsed().as_secs_f64() * 1000.0,
            "backend": capture::BACKEND,
            "mime_type": "image/png",
        });
        DispatchOutcome {
            response: ResponseHeader::success_with_binary(id, result, png.len() as u64),
            binary: png,
            close: None,
        }
    }

    fn binding(&self) -> &Binding {
        self.binding
            .as_ref()
            .expect("an initialized dispatcher is bound")
    }

    fn validate_grant(&self, grant_id: &str, revoke_epoch: u64) -> Result<(), Failure> {
        self.binding().validate(grant_id, revoke_epoch)
    }

    fn safety_error(&self, id: String, failure: Failure) -> DispatchOutcome {
        self.body_error(
            id,
            ErrorBody {
                code: failure.code().to_owned(),
                message: failure.message().to_owned(),
                retryable: failure != Failure::Revoked,
                category: "safety".to_owned(),
            },
        )
    }

    fn body_error(&self, id: String, body: ErrorBody) -> DispatchOutcome {
        DispatchOutcome {
            response: ResponseHeader::error(id, body),
            binary: Vec::new(),
            close: None,
        }
    }

    fn invalid(&self, id: String, message: impl Into<String>) -> DispatchOutcome {
        self.error(id, "INVALID_PARAMS", message, None)
    }

    // ---------------------------------------------------------------- keyboard

    fn ensure_keyboard(
        &mut self,
        hold_max_seconds: u64,
    ) -> Result<(Arc<dyn KeyboardService>, bool), &'static str> {
        if self
            .keyboard
            .as_ref()
            .is_some_and(|(_, keyboard)| keyboard.is_closed())
        {
            self.keyboard.take();
        }
        if let Some((configured, keyboard)) = self.keyboard.as_ref() {
            if *configured != hold_max_seconds {
                return Err("hold_max_seconds cannot change while the native keyboard is open");
            }
            return Ok((Arc::clone(keyboard), true));
        }
        // SendInput needs no device to be created, so there is no settle.
        let keyboard: Arc<dyn KeyboardService> = Arc::new(Keyboard::new(
            SendInputKeyboard,
            SystemClock::default(),
            Duration::from_secs(hold_max_seconds),
        ));
        let weak: Weak<dyn FailClosed> = Arc::downgrade(&keyboard) as Weak<dyn KeyboardService>;
        self.binding().register_fail_closed(weak);
        self.keyboard = Some((hold_max_seconds, Arc::clone(&keyboard)));
        Ok((keyboard, false))
    }

    fn keyboard_for(
        &mut self,
        id: &str,
        grant: &KeyboardGrantParams,
    ) -> Result<Arc<dyn KeyboardService>, Box<DispatchOutcome>> {
        if let Err(message) = grant.validate() {
            return Err(Box::new(self.invalid(id.to_owned(), message)));
        }
        if let Err(failure) = self.validate_grant(&grant.grant_id, grant.revoke_epoch) {
            return Err(Box::new(self.safety_error(id.to_owned(), failure)));
        }
        self.ensure_keyboard(grant.hold_max_seconds)
            .map(|(keyboard, _)| keyboard)
            .map_err(|message| Box::new(self.invalid(id.to_owned(), message)))
    }

    fn keyboard_ensure(&mut self, id: String, params: Value) -> DispatchOutcome {
        let params = match serde_json::from_value::<KeyboardGrantParams>(params) {
            Ok(params) => params,
            Err(error) => return self.invalid(id, format!("invalid keyboard parameters: {error}")),
        };
        if let Err(message) = params.validate() {
            return self.invalid(id, message);
        }
        if let Err(failure) = self.validate_grant(&params.grant_id, params.revoke_epoch) {
            return self.safety_error(id, failure);
        }
        match self.ensure_keyboard(params.hold_max_seconds) {
            Ok((keyboard, already)) => self.success(
                id,
                json!({
                    "already": already,
                    "waited_seconds": 0.0,
                    "held": keyboard.held(),
                    "backend": win_input::BACKEND,
                }),
                None,
            ),
            Err(message) => self.invalid(id, message),
        }
    }

    fn keyboard_action(&mut self, id: String, params: Value, action: KeyAction) -> DispatchOutcome {
        let params = match serde_json::from_value::<KeyboardActionParams>(params) {
            Ok(params) => params,
            Err(error) => return self.invalid(id, format!("invalid keyboard parameters: {error}")),
        };
        if let Err(message) = params.validate() {
            return self.invalid(id, message);
        }
        let keyboard = match self.keyboard_for(&id, &params.grant) {
            Ok(keyboard) => keyboard,
            Err(outcome) => return *outcome,
        };
        let result = match action {
            KeyAction::Key => keyboard.key(&params.combo),
            KeyAction::Down => keyboard.key_down(&params.combo),
            KeyAction::Up => keyboard.key_up(&params.combo),
        };
        match result {
            Ok(()) => self.success(
                id,
                json!({"held": keyboard.held(), "backend": win_input::BACKEND}),
                None,
            ),
            Err(error) => self.keyboard_operation_error(id, error),
        }
    }

    /// Text as characters (`KEYEVENTF_UNICODE`), independent of the
    /// keyboard layout. The text is the request payload, never a header
    /// field that could reach a log.
    fn keyboard_type(&mut self, id: String, params: Value, text: &[u8]) -> DispatchOutcome {
        let params = match serde_json::from_value::<KeyboardGrantParams>(params) {
            Ok(params) => params,
            Err(error) => return self.invalid(id, format!("invalid keyboard parameters: {error}")),
        };
        let Ok(text) = std::str::from_utf8(text) else {
            return self.invalid(id, "the text payload is not valid UTF-8");
        };
        if text.chars().count() > MAX_TYPE_CHARS {
            return self.invalid(
                id,
                format!("the text is longer than {MAX_TYPE_CHARS} characters"),
            );
        }
        let keyboard = match self.keyboard_for(&id, &params) {
            Ok(keyboard) => keyboard,
            Err(outcome) => return *outcome,
        };
        match keyboard.type_text(text) {
            Ok(typed) => self.success(
                id,
                json!({"typed": typed, "held": keyboard.held(), "backend": win_input::BACKEND}),
                None,
            ),
            Err(error) => self.keyboard_operation_error(id, error),
        }
    }

    fn keyboard_held(&self, id: String) -> DispatchOutcome {
        let held = self
            .keyboard
            .as_ref()
            .map_or_else(Vec::new, |(_, keyboard)| keyboard.held());
        self.success(
            id,
            json!({"held": held, "backend": win_input::BACKEND}),
            None,
        )
    }

    /// A cleanup path: no fresh grant needed.
    fn keyboard_release_all(&self, id: String) -> DispatchOutcome {
        let released = match self.keyboard.as_ref() {
            None => Ok(Vec::new()),
            Some((_, keyboard)) => keyboard.release_all(),
        };
        match released {
            Ok(released) => self.success(
                id,
                json!({"released": released, "backend": win_input::BACKEND}),
                None,
            ),
            Err(error) => self.keyboard_operation_error(id, error),
        }
    }

    fn keyboard_take_auto_released(&self, id: String) -> DispatchOutcome {
        let released = self
            .keyboard
            .as_ref()
            .map_or_else(Vec::new, |(_, keyboard)| keyboard.take_auto_released());
        self.success(
            id,
            json!({"released": released, "backend": win_input::BACKEND}),
            None,
        )
    }

    fn keyboard_operation_error(&self, id: String, error: KeyboardError) -> DispatchOutcome {
        let (code, category, retryable) = match &error {
            KeyboardError::UnknownKey(_)
            | KeyboardError::EmptyCombo
            | KeyboardError::HeldWhileTyping => return self.invalid(id, error.to_string()),
            KeyboardError::Closed => ("BACKEND_UNAVAILABLE", "capability", true),
            KeyboardError::Device(_) => ("EXECUTION_UNKNOWN", "execution", false),
        };
        self.body_error(
            id,
            ErrorBody {
                code: code.to_owned(),
                message: format!("native keyboard: {error}"),
                retryable,
                category: category.to_owned(),
            },
        )
    }

    fn close_keyboard(&mut self) {
        if let Some((_, keyboard)) = self.keyboard.take() {
            let _ = keyboard.close();
        }
    }

    // ----------------------------------------------------------------- pointer

    fn pointer_geometry(
        &self,
        expected_topology: &str,
    ) -> Result<PointerGeometry, PointerRequestError> {
        let monitors = display::monitors().map_err(PointerRequestError::DisplayUnknown)?;
        if pcbridge_core::display::topology_id(&monitors) != expected_topology {
            return Err(PointerRequestError::DisplayChanged);
        }
        let (width, height) = pcbridge_core::display::canvas_size(&monitors);
        PointerGeometry::new(width, height).map_err(PointerRequestError::Geometry)
    }

    fn ensure_pointer(
        &mut self,
        topology_id: &str,
        geometry: PointerGeometry,
        config: PointerConfig,
    ) -> Result<Arc<dyn PointerService>, PointerRequestError> {
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
            return Ok(Arc::clone(pointer));
        }
        let state_file = self
            .state_dir
            .as_ref()
            .expect("an initialized dispatcher has a state directory")
            .join("pointer.json");
        let pointer: Arc<dyn PointerService> = Arc::new(Pointer::new(
            SendInputPointer::new(geometry),
            SystemPointerClock::default(),
            geometry,
            config,
            Some(state_file),
        ));
        let weak: Weak<dyn FailClosed> = Arc::downgrade(&pointer) as Weak<dyn PointerService>;
        self.binding().register_fail_closed(weak);
        self.pointer = Some((topology_id.to_owned(), config, Arc::clone(&pointer)));
        Ok(pointer)
    }

    fn ensure_relative(&mut self) -> Arc<dyn RelativeService> {
        if self
            .relative
            .as_ref()
            .is_some_and(|relative| relative.is_closed())
        {
            self.relative.take();
        }
        if let Some(relative) = self.relative.as_ref() {
            return Arc::clone(relative);
        }
        let relative: Arc<dyn RelativeService> = Arc::new(Relative::new(
            SendInputRelative,
            SystemPointerClock::default(),
            POINTER_STEP,
        ));
        let weak: Weak<dyn FailClosed> = Arc::downgrade(&relative) as Weak<dyn RelativeService>;
        self.binding().register_fail_closed(weak);
        self.relative = Some(Arc::clone(&relative));
        relative
    }

    fn pointer_for(
        &mut self,
        params: &PointerGrantParams,
    ) -> Result<Arc<dyn PointerService>, PointerRequestError> {
        self.validate_grant(&params.grant_id, params.revoke_epoch)
            .map_err(PointerRequestError::Lifecycle)?;
        let geometry = self.pointer_geometry(&params.topology_id)?;
        self.ensure_pointer(&params.topology_id, geometry, params.config())
    }

    /// Parse, check and open: the common start of every pointer request.
    fn pointer_request<T: serde::de::DeserializeOwned>(
        &mut self,
        id: &str,
        params: Value,
        fields: &[&str],
        grant: impl Fn(&T) -> &PointerGrantParams,
    ) -> Result<(T, Arc<dyn PointerService>), Box<DispatchOutcome>> {
        let params = parse_pointer_params::<T>(params, fields).map_err(|error| {
            Box::new(self.invalid(
                id.to_owned(),
                format!("invalid pointer parameters: {error}"),
            ))
        })?;
        if let Err(message) = grant(&params).validate() {
            return Err(Box::new(self.invalid(id.to_owned(), message)));
        }
        match self.pointer_for(grant(&params)) {
            Ok(pointer) => Ok((params, pointer)),
            Err(error) => Err(Box::new(self.pointer_request_error(id.to_owned(), error))),
        }
    }

    fn pointer_ensure(&mut self, id: String, params: Value) -> DispatchOutcome {
        let params =
            match parse_pointer_params::<PointerEnsureParams>(params, &["pointer", "relative"]) {
                Ok(params) => params,
                Err(error) => {
                    return self.invalid(id, format!("invalid pointer parameters: {error}"));
                }
            };
        if let Err(message) = params.grant.validate() {
            return self.invalid(id, message);
        }
        let already = self.pointer.is_some();
        let mut pointer = None;
        if params.pointer {
            match self.pointer_for(&params.grant) {
                Ok(opened) => pointer = Some(opened),
                Err(error) => return self.pointer_request_error(id, error),
            }
        } else if let Err(failure) =
            self.validate_grant(&params.grant.grant_id, params.grant.revoke_epoch)
        {
            return self.safety_error(id, failure);
        }
        if params.relative {
            self.ensure_relative();
        }
        self.success(
            id,
            json!({
                "already": already,
                "waited_seconds": 0.0,
                "position": pointer.as_ref().and_then(|pointer| pointer.position()),
                "held": pointer.as_ref().map_or_else(Vec::new, |pointer| pointer.held()),
                "relative": params.relative,
                "backend": win_input::BACKEND,
            }),
            None,
        )
    }

    fn pointer_move_by(&mut self, id: String, params: Value) -> DispatchOutcome {
        let params = match parse_relative_params::<RelativeMoveParams>(params, &["dx", "dy"]) {
            Ok(params) => params,
            Err(error) => {
                return self.invalid(id, format!("invalid relative pointer parameters: {error}"));
            }
        };
        let grant: &RelativeGrantParams = &params.grant;
        if let Err(message) = grant.validate() {
            return self.invalid(id, message);
        }
        if let Err(failure) = self.validate_grant(&grant.grant_id, grant.revoke_epoch) {
            return self.safety_error(id, failure);
        }
        let relative = self.ensure_relative();
        // Forget before sending: after a partial failure "unknown" is the
        // true answer (the Linux dispatcher's reasoning).
        if let Some((_, _, pointer)) = self.pointer.as_ref() {
            pointer.note_external_motion();
        } else if let Some(directory) = self.state_dir.as_ref() {
            let _ = std::fs::remove_file(directory.join("pointer.json"));
        }
        match relative.move_by(params.dx, params.dy) {
            Ok(sent) => self.success(
                id,
                json!({"sent": [sent.0, sent.1], "position": Value::Null, "backend": win_input::BACKEND}),
                None,
            ),
            Err(error) => self.pointer_operation_error(id, error),
        }
    }

    fn pointer_move(&mut self, id: String, params: Value) -> DispatchOutcome {
        let (params, pointer) = match self.pointer_request::<PointerMoveParams>(
            &id,
            params,
            &["x", "y", "smooth"],
            |params| &params.grant,
        ) {
            Ok(ready) => ready,
            Err(outcome) => return *outcome,
        };
        match pointer.move_to(params.x, params.y, params.smooth, 1) {
            Ok(position) => self.success(
                id,
                json!({"position": position, "backend": win_input::BACKEND}),
                None,
            ),
            Err(error) => self.pointer_operation_error(id, error),
        }
    }

    fn pointer_click(&mut self, id: String, params: Value) -> DispatchOutcome {
        let (params, pointer) = match self.pointer_request::<PointerClickParams>(
            &id,
            params,
            &["button", "count", "hold_ms"],
            |params| &params.grant,
        ) {
            Ok(ready) => ready,
            Err(outcome) => return *outcome,
        };
        let hold = match params.hold_ms {
            None => DEFAULT_CLICK_HOLD,
            Some(ms) if ms <= MAX_CLICK_HOLD_MS => Duration::from_millis(ms),
            Some(ms) => {
                return self.invalid(
                    id,
                    format!("hold_ms must be at most {MAX_CLICK_HOLD_MS}, got {ms}"),
                );
            }
        };
        match pointer.click(&params.button, params.count, hold) {
            Ok(()) => self.success(
                id,
                json!({"held": pointer.held(), "backend": win_input::BACKEND}),
                None,
            ),
            Err(error) => self.pointer_operation_error(id, error),
        }
    }

    fn pointer_drag(&mut self, id: String, params: Value) -> DispatchOutcome {
        let (params, pointer) = match self.pointer_request::<PointerDragParams>(
            &id,
            params,
            &["x1", "y1", "x2", "y2", "button"],
            |params| &params.grant,
        ) {
            Ok(ready) => ready,
            Err(outcome) => return *outcome,
        };
        match pointer.drag(params.x1, params.y1, params.x2, params.y2, &params.button) {
            Ok(()) => self.success(
                id,
                json!({"position": pointer.position(), "backend": win_input::BACKEND}),
                None,
            ),
            Err(error) => self.pointer_operation_error(id, error),
        }
    }

    fn pointer_scroll(&mut self, id: String, params: Value) -> DispatchOutcome {
        let (params, pointer) = match self.pointer_request::<PointerScrollParams>(
            &id,
            params,
            &["amount", "horizontal"],
            |params| &params.grant,
        ) {
            Ok(ready) => ready,
            Err(outcome) => return *outcome,
        };
        match pointer.scroll(params.amount, params.horizontal) {
            Ok(()) => self.success(
                id,
                json!({"position": pointer.position(), "backend": win_input::BACKEND}),
                None,
            ),
            Err(error) => self.pointer_operation_error(id, error),
        }
    }

    fn pointer_button(&mut self, id: String, params: Value, down: bool) -> DispatchOutcome {
        let (params, pointer) =
            match self.pointer_request::<PointerButtonParams>(&id, params, &["button"], |params| {
                &params.grant
            }) {
                Ok(ready) => ready,
                Err(outcome) => return *outcome,
            };
        let result = if down {
            pointer.mouse_down(&params.button)
        } else {
            pointer.mouse_up(&params.button)
        };
        match result {
            Ok(()) => self.success(
                id,
                json!({"held": pointer.held(), "backend": win_input::BACKEND}),
                None,
            ),
            Err(error) => self.pointer_operation_error(id, error),
        }
    }

    fn pointer_held(&self, id: String) -> DispatchOutcome {
        let held = self
            .pointer
            .as_ref()
            .map_or_else(Vec::new, |(_, _, pointer)| pointer.held());
        self.success(
            id,
            json!({"held": held, "backend": win_input::BACKEND}),
            None,
        )
    }

    fn pointer_release_all(&self, id: String) -> DispatchOutcome {
        let released = match self.pointer.as_ref() {
            None => Ok(Vec::new()),
            Some((_, _, pointer)) => pointer.release_all(),
        };
        match released {
            Ok(released) => self.success(
                id,
                json!({"released": released, "backend": win_input::BACKEND}),
                None,
            ),
            Err(error) => self.pointer_operation_error(id, error),
        }
    }

    fn pointer_take_auto_released(&self, id: String) -> DispatchOutcome {
        let released = self
            .pointer
            .as_ref()
            .map_or_else(Vec::new, |(_, _, pointer)| pointer.take_auto_released());
        self.success(
            id,
            json!({"released": released, "backend": win_input::BACKEND}),
            None,
        )
    }

    fn pointer_position(&self, id: String) -> DispatchOutcome {
        let position = self
            .pointer
            .as_ref()
            .and_then(|(_, _, pointer)| pointer.position());
        self.success(
            id,
            json!({"position": position, "backend": win_input::BACKEND}),
            None,
        )
    }

    fn pointer_request_error(&self, id: String, error: PointerRequestError) -> DispatchOutcome {
        let (code, message) = match error {
            PointerRequestError::Lifecycle(failure) => return self.safety_error(id, failure),
            PointerRequestError::SettingsChanged => {
                return self.invalid(
                    id,
                    "pointer settings cannot change while the native pointer is open",
                );
            }
            PointerRequestError::Geometry(error) => return self.invalid(id, error.to_string()),
            PointerRequestError::DisplayChanged => (
                "DISPLAY_CHANGED",
                "the pointer topology no longer matches the resolved coordinate".to_owned(),
            ),
            PointerRequestError::DisplayUnknown(message) => ("DISPLAY_MAPPING_UNKNOWN", message),
        };
        self.body_error(
            id,
            ErrorBody {
                code: code.to_owned(),
                message,
                retryable: true,
                category: "coordinate".to_owned(),
            },
        )
    }

    fn pointer_operation_error(&self, id: String, error: PointerError) -> DispatchOutcome {
        let (code, category, retryable) = match &error {
            PointerError::InvalidGeometry
            | PointerError::UnknownButton(_)
            | PointerError::InvalidClickCount
            | PointerError::InvalidClickHold => return self.invalid(id, error.to_string()),
            PointerError::Closed => ("BACKEND_UNAVAILABLE", "capability", true),
            PointerError::Device(_) => ("EXECUTION_UNKNOWN", "execution", false),
        };
        self.body_error(
            id,
            ErrorBody {
                code: code.to_owned(),
                message: format!("native pointer: {error}"),
                retryable,
                category: category.to_owned(),
            },
        )
    }

    fn close_pointer(&mut self) {
        if let Some((_, _, pointer)) = self.pointer.take() {
            let _ = pointer.close();
        }
    }

    fn close_relative(&mut self) {
        if let Some(relative) = self.relative.take() {
            relative.close();
        }
    }

    // --------------------------------------------------------------- clipboard

    fn clipboard_check(
        &self,
        id: &str,
        grant_id: &str,
        revoke_epoch: u64,
    ) -> Option<DispatchOutcome> {
        if grant_id.is_empty() || grant_id.len() > 256 {
            return Some(self.invalid(id.to_owned(), "grant_id must be nonempty and bounded"));
        }
        self.validate_grant(grant_id, revoke_epoch)
            .err()
            .map(|failure| self.safety_error(id.to_owned(), failure))
    }

    fn clipboard_read(&self, id: String, params: Value) -> DispatchOutcome {
        let params = match serde_json::from_value::<ClipboardGrantParams>(params) {
            Ok(params) => params,
            Err(error) => {
                return self.invalid(id, format!("invalid clipboard parameters: {error}"));
            }
        };
        if let Some(refused) = self.clipboard_check(&id, &params.grant_id, params.revoke_epoch) {
            return refused;
        }
        let read = clipboard::read();
        // A revoke while reading must not be answered with the clipboard.
        if let Err(failure) = self.validate_grant(&params.grant_id, params.revoke_epoch) {
            return self.safety_error(id, failure);
        }
        match read {
            Ok(Some(data)) => {
                let result = json!({"empty": false, "mime": clipboard::TEXT_MIME, "backend": clipboard::BACKEND});
                DispatchOutcome {
                    response: ResponseHeader::success_with_binary(id, result, data.len() as u64),
                    binary: data,
                    close: None,
                }
            }
            Ok(None) => self.success(
                id,
                json!({"empty": true, "mime": null, "backend": clipboard::BACKEND}),
                None,
            ),
            Err(error) => self.clipboard_error(id, error),
        }
    }

    fn clipboard_write(&self, id: String, params: Value, data: &[u8]) -> DispatchOutcome {
        let params = match serde_json::from_value::<ClipboardWriteParams>(params) {
            Ok(params) => params,
            Err(error) => {
                return self.invalid(id, format!("invalid clipboard parameters: {error}"));
            }
        };
        if let Some(refused) = self.clipboard_check(&id, &params.grant_id, params.revoke_epoch) {
            return refused;
        }
        if params.mime.is_empty()
            || params.mime.len() > 256
            || params.mime.chars().any(char::is_control)
        {
            return self.invalid(id, "mime must be 1 to 256 bytes without control characters");
        }
        match clipboard::write(&params.mime, data) {
            Ok(()) => self.success(
                id,
                json!({"written": data.len(), "backend": clipboard::BACKEND}),
                None,
            ),
            Err(error) => self.clipboard_error(id, error),
        }
    }

    fn clipboard_clear(&self, id: String, params: Value) -> DispatchOutcome {
        let params = match serde_json::from_value::<ClipboardGrantParams>(params) {
            Ok(params) => params,
            Err(error) => {
                return self.invalid(id, format!("invalid clipboard parameters: {error}"));
            }
        };
        if let Some(refused) = self.clipboard_check(&id, &params.grant_id, params.revoke_epoch) {
            return refused;
        }
        match clipboard::clear() {
            Ok(()) => self.success(
                id,
                json!({"cleared": true, "backend": clipboard::BACKEND}),
                None,
            ),
            Err(error) => self.clipboard_error(id, error),
        }
    }

    fn clipboard_error(&self, id: String, error: ClipboardError) -> DispatchOutcome {
        let (code, category, retryable) = match &error {
            ClipboardError::Busy => ("BUSY", "execution", true),
            ClipboardError::NotText(_) | ClipboardError::TooLarge => {
                ("UNSUPPORTED", "capability", false)
            }
            ClipboardError::InvalidUtf8 => return self.invalid(id, error.to_string()),
            ClipboardError::Failed(_) => ("EXECUTION_UNKNOWN", "execution", false),
        };
        self.body_error(
            id,
            ErrorBody {
                code: code.to_owned(),
                message: error.to_string(),
                retryable,
                category: category.to_owned(),
            },
        )
    }

    fn typed_error(
        &self,
        id: String,
        code: &str,
        message: &str,
        category: &str,
    ) -> DispatchOutcome {
        DispatchOutcome {
            response: ResponseHeader::error(
                id,
                ErrorBody {
                    code: code.to_owned(),
                    message: message.to_owned(),
                    retryable: matches!(
                        code,
                        "DISPLAY_CHANGED" | "CAPTURE_FAILED" | "DISPLAY_MAPPING_UNKNOWN"
                    ),
                    category: category.to_owned(),
                },
            ),
            binary: Vec::new(),
            close: None,
        }
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
}

impl Drop for Dispatcher {
    /// Held keys and buttons are released when the connection ends.
    fn drop(&mut self) {
        self.close_keyboard();
        self.close_pointer();
        self.close_relative();
    }
}

/// The methods the Linux dispatcher serves: known here, with no backend yet.
fn is_known(method: &str) -> bool {
    method.starts_with("capture.")
        || method.starts_with("input.")
        || method.starts_with("clipboard.")
        || method.starts_with("accessibility.")
        || method.starts_with("window.")
}

fn ping_result(params: Value) -> Value {
    match params.get("nonce") {
        Some(nonce) => json!({"pong": true, "nonce": nonce}),
        None => json!({"pong": true}),
    }
}
