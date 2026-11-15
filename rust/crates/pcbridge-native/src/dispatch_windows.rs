//! The protocol dispatcher on Windows (docs/native/protocol-v1.md).
//!
//! The same requests and answers as the Linux dispatcher, served by Win32
//! instead of AT-SPI, PipeWire, uinput and X11. It grows with the Windows
//! port's W6 stages; a method whose backend has not arrived yet answers
//! `BACKEND_UNAVAILABLE`, which the Python side reports as an unavailable
//! capability rather than a failure.

use std::path::{Path, PathBuf};
use std::sync::{Arc, Weak};
use std::time::{Duration, Instant};

use pcbridge_core::{
    ErrorBody, Frame, PROTOCOL_MAJOR, PROTOCOL_MINOR, ProtocolError, RequestHeader, ResponseHeader,
};
use serde::Deserialize;
use serde::de::DeserializeOwned;
use serde_json::{Value, json};

use crate::accessibility::requests::{
    AccessibilityActParams, AccessibilityDumpParams, AccessibilityGrant, AccessibilityGrantParams,
    AccessibilityRead, AccessibilitySetTextParams, DUMP_DEADLINE_MS, DUMP_DEADLINE_MS_MAX,
    DumpRegistry, FocusParams, MAX_DUMP_NODES, accessibility_error_body, action_target,
};
use crate::accessibility::{self, DumpRequest, FocusHint, action};
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
use crate::platform::windows::uia::{self, Uia};
use crate::platform::windows::{capture, display, process, window};
use windows::Win32::Foundation::HWND;

/// The backend `window.focused` and `window.activate` report.
const WINDOW_BACKEND: &str = "windows.user32";

/// The most text one `input.keyboard.type` request may carry. A character
/// takes 10 ms by its keys and about 90 ms as a Unicode packet, so even a
/// text of packets only types in 90 s, under the 110 s call limit.
const MAX_TYPE_CHARS: usize = 1_000;

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
            "accessibility.read",
            "accessibility.action",
            "accessibility.focus_hint",
            "window.focused",
            "window.activate",
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
                supported("accessibility.read", "os.accessibility", uia::BACKEND),
                supported("accessibility.windows", "os.accessibility", uia::BACKEND),
                supported("accessibility.action", "os.accessibility", uia::BACKEND),
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

/// `window.activate`: the grant and the window to bring to the front.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct WindowActivateParams {
    grant_id: String,
    revoke_epoch: u64,
    target: String,
}

/// A window as `window.focused` and `window.activate` answer, the fields
/// the Linux dispatcher gives.
fn describe_window(hwnd: Option<HWND>) -> Value {
    match hwnd {
        Some(hwnd) => {
            let pid = process::window_pid(hwnd).unwrap_or(0);
            json!({
                "found": true,
                "app": process::name(pid).unwrap_or_default(),
                "title": uia::window_title(hwnd),
                "geometry": uia::window_frame(hwnd).map(|(x, y, w, h)| json!([x, y, w, h])),
            })
        }
        None => json!({"found": false, "app": "", "title": "", "geometry": null}),
    }
}

/// Case, accents of spacing and punctuation do not decide a match.
fn normalize(text: &str) -> String {
    text.split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase()
}

/// 3 exact, 2 prefix or suffix, 1 substring, 0 no match: X11's rule.
fn field_score(query: &str, value: &str) -> u8 {
    if value.is_empty() {
        0
    } else if value == query {
        3
    } else if value.starts_with(query) || value.ends_with(query) {
        2
    } else {
        u8::from(value.contains(query))
    }
}

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
    /// Connected by the first accessibility request, never at startup.
    accessibility: Option<Uia>,
    /// What the dumps listed, for the actions that name them.
    accessibility_dumps: DumpRegistry,
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
            accessibility: None,
            accessibility_dumps: DumpRegistry::default(),
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
            "accessibility.dump" => self.accessibility_dump(id, params),
            "accessibility.windows" => self.accessibility_read(id, params, &|tree, hint| {
                accessibility::windows_hinted(tree, hint)
            }),
            "accessibility.focused" => self.accessibility_read(id, params, &|tree, hint| {
                accessibility::focused_hinted(tree, hint)
            }),
            "accessibility.act" => self.accessibility_act(id, params),
            "accessibility.set_text" => self.accessibility_set_text(id, params, &binary),
            "window.focused" => self.window_focused(id, params),
            "window.activate" => self.window_activate(id, params),
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
        // Releasing is always allowed; pressing goes to the window in front.
        if !matches!(action, KeyAction::Up)
            && let Some(refused) = self.elevated_target(&id, process::foreground_pid())
        {
            return refused;
        }
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
        if let Some(refused) = self.elevated_target(&id, process::foreground_pid()) {
            return refused;
        }
        // A Windows line break is one Enter, not two.
        let text = text.replace("\r\n", "\n");
        match keyboard.type_text(&text) {
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
        if let Some(refused) = self.elevated_target(&id, process::pid_under_cursor()) {
            return refused;
        }
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
        if let Some(refused) = self.elevated_target(&id, process::pid_at(params.x1, params.y1)) {
            return refused;
        }
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
        if let Some(refused) = self.elevated_target(&id, process::pid_under_cursor()) {
            return refused;
        }
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
        if down && let Some(refused) = self.elevated_target(&id, process::pid_under_cursor()) {
            return refused;
        }
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

    // ----------------------------------------------------------- accessibility

    fn accessibility_tree(&mut self) -> Result<&Uia, ErrorBody> {
        if self.accessibility.is_none() {
            let uia = Uia::connect().map_err(|error| accessibility_error_body(&error))?;
            self.accessibility = Some(uia);
        }
        Ok(self
            .accessibility
            .as_ref()
            .expect("UI Automation was just connected"))
    }

    fn accessibility_grant<T: DeserializeOwned + AccessibilityGrant>(
        &self,
        id: &str,
        params: Value,
    ) -> Result<T, Box<DispatchOutcome>> {
        let params = serde_json::from_value::<T>(params).map_err(|error| {
            Box::new(self.invalid(
                id.to_owned(),
                format!("invalid accessibility parameters: {error}"),
            ))
        })?;
        if params.grant_id().is_empty() || params.grant_id().len() > 256 {
            return Err(Box::new(
                self.invalid(id.to_owned(), "grant_id must be nonempty and bounded"),
            ));
        }
        self.validate_grant(params.grant_id(), params.revoke_epoch())
            .map_err(|failure| Box::new(self.safety_error(id.to_owned(), failure)))?;
        Ok(params)
    }

    /// The focused window as Windows knows it: the foreground window's
    /// process and title, which wins over any hint, as X11's does.
    fn focus_hint() -> Option<FocusHint> {
        let hwnd = window::foreground()?;
        let pid = process::window_pid(hwnd)?;
        Some(FocusHint {
            pid,
            title: uia::window_title(hwnd),
            apps: Vec::new(),
        })
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
        // UIA shows an elevated window's frame and nothing inside it; say
        // why instead of returning an empty list.
        let focused = matches!(params.target.trim(), "" | "focused");
        if focused && let Some(refused) = self.elevated_target(&id, process::foreground_pid()) {
            return refused;
        }
        let request = DumpRequest {
            focus: Self::focus_hint().or_else(|| params.focus.as_ref().map(FocusParams::hint)),
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
            deadline: Some(Instant::now() + Duration::from_millis(deadline_ms)),
            deadline_seconds: deadline_ms.div_ceil(1000),
        };
        let read = match self.accessibility_tree() {
            Ok(tree) => accessibility::read_dump(tree, &request),
            Err(error) => return self.body_error(id, error),
        };
        if let Err(failure) = self.validate_grant(&params.grant_id, params.revoke_epoch) {
            return self.safety_error(id, failure);
        }
        match read {
            Ok((mut dump, record)) => {
                let snapshot = self.accessibility_dumps.insert(record);
                dump["snapshot"] = Value::String(snapshot);
                dump["backend"] = json!(uia::BACKEND);
                self.success(id, dump, None)
            }
            Err(error) => self.body_error(id, accessibility_error_body(&error)),
        }
    }

    fn accessibility_read(
        &mut self,
        id: String,
        params: Value,
        read: &AccessibilityRead,
    ) -> DispatchOutcome {
        let params = match self.accessibility_grant::<AccessibilityGrantParams>(&id, params) {
            Ok(params) => params,
            Err(outcome) => return *outcome,
        };
        let hint = Self::focus_hint().or_else(|| params.focus.as_ref().map(FocusParams::hint));
        let result = match self.accessibility_tree() {
            Ok(tree) => read(tree, hint.as_ref()),
            Err(error) => return self.body_error(id, error),
        };
        if let Err(failure) = self.validate_grant(&params.grant_id, params.revoke_epoch) {
            return self.safety_error(id, failure);
        }
        match result {
            Ok(result) => self.success(id, result, None),
            Err(error) => self.body_error(id, accessibility_error_body(&error)),
        }
    }

    /// Click: resolve, check the grant once more, then send the action once.
    fn accessibility_act(&mut self, id: String, params: Value) -> DispatchOutcome {
        let params = match self.accessibility_grant::<AccessibilityActParams>(&id, params) {
            Ok(params) => params,
            Err(outcome) => return *outcome,
        };
        let (record, node) = match self
            .accessibility_dumps
            .node(&params.snapshot, &params.reference)
        {
            Ok(found) => found,
            Err(error) => return self.body_error(id, accessibility_error_body(&error)),
        };
        if let Err(error) = self.accessibility_tree() {
            return self.body_error(id, error);
        }
        let tree = self.accessibility.as_ref().expect("connected above");
        let target = action_target(&record, &node);
        let prepared = match action::prepare_act(tree, &target, &params.action) {
            Ok(prepared) => prepared,
            Err(error) => return self.body_error(id, accessibility_error_body(&error)),
        };
        if let Err(failure) = self.validate_grant(&params.grant_id, params.revoke_epoch) {
            return self.safety_error(id, failure);
        }
        match action::perform_act(tree, prepared) {
            Ok(result) => self.success(id, result, None),
            Err(error) => self.body_error(id, accessibility_error_body(&error)),
        }
    }

    /// Replace a field's text; the text is the request payload.
    fn accessibility_set_text(
        &mut self,
        id: String,
        params: Value,
        data: &[u8],
    ) -> DispatchOutcome {
        let params = match self.accessibility_grant::<AccessibilitySetTextParams>(&id, params) {
            Ok(params) => params,
            Err(outcome) => return *outcome,
        };
        let Ok(text) = std::str::from_utf8(data) else {
            return self.invalid(id, "the text is not UTF-8");
        };
        let (record, node) = match self
            .accessibility_dumps
            .node(&params.snapshot, &params.reference)
        {
            Ok(found) => found,
            Err(error) => return self.body_error(id, accessibility_error_body(&error)),
        };
        if let Err(error) = self.accessibility_tree() {
            return self.body_error(id, error);
        }
        let tree = self.accessibility.as_ref().expect("connected above");
        let target = action_target(&record, &node);
        let prepared = match action::prepare_set_text(tree, &target) {
            Ok(prepared) => prepared,
            Err(error) => return self.body_error(id, accessibility_error_body(&error)),
        };
        if let Err(failure) = self.validate_grant(&params.grant_id, params.revoke_epoch) {
            return self.safety_error(id, failure);
        }
        match action::perform_set_text(tree, prepared, text, action::TEXT_SETTLE) {
            Ok(result) => self.success(id, result, None),
            Err(error) => self.body_error(id, accessibility_error_body(&error)),
        }
    }

    // ----------------------------------------------------------------- windows

    /// The focused window with its frame, under the grant: a title is
    /// screen content.
    fn window_focused(&mut self, id: String, params: Value) -> DispatchOutcome {
        let params = match serde_json::from_value::<ClipboardGrantParams>(params) {
            Ok(params) => params,
            Err(error) => return self.invalid(id, format!("invalid window parameters: {error}")),
        };
        if let Some(refused) = self.clipboard_check(&id, &params.grant_id, params.revoke_epoch) {
            return refused;
        }
        let mut result = describe_window(window::foreground());
        result["backend"] = json!(WINDOW_BACKEND);
        self.success(id, result, None)
    }

    /// Activate the one window `target` names: title or application name,
    /// exact beats prefix or suffix beats substring, a tie activates
    /// nothing. Windows lets a process bring a window to the front only
    /// when it sent the last input, so one key no application sees
    /// (`VK_F24`) goes first (measured in W0). Confirmed by reading the
    /// foreground back, never assumed.
    fn window_activate(&mut self, id: String, params: Value) -> DispatchOutcome {
        let params = match serde_json::from_value::<WindowActivateParams>(params) {
            Ok(params) => params,
            Err(error) => return self.invalid(id, format!("invalid window parameters: {error}")),
        };
        if let Some(refused) = self.clipboard_check(&id, &params.grant_id, params.revoke_epoch) {
            return refused;
        }
        let query = normalize(&params.target);
        let ranked: Vec<(u8, HWND)> = if query.is_empty() || params.target.chars().count() > 256 {
            Vec::new()
        } else {
            uia::top_windows()
                .into_iter()
                .filter_map(|window| {
                    let title = normalize(&uia::window_title(window.hwnd));
                    let app = normalize(&process::name(window.pid).unwrap_or_default());
                    let score = field_score(&query, &title).max(field_score(&query, &app));
                    (score > 0).then_some((score, window.hwnd))
                })
                .collect()
        };
        let best = ranked.iter().map(|(score, _)| *score).max().unwrap_or(0);
        let top: Vec<HWND> = ranked
            .iter()
            .filter(|(score, _)| *score == best)
            .map(|(_, hwnd)| *hwnd)
            .collect();
        let mut activated = false;
        if let [hwnd] = top.as_slice() {
            activated = window::activate(*hwnd);
        }
        let mut result = describe_window(window::foreground());
        result["activated"] = json!(activated);
        result["ambiguous"] = json!(top.len() > 1);
        result["backend"] = json!(WINDOW_BACKEND);
        self.success(id, result, None)
    }

    /// The refusal of a write whose target runs elevated, before anything
    /// is sent: Windows would drop it without a word.
    fn elevated_target(&self, id: &str, pid: Option<u32>) -> Option<DispatchOutcome> {
        let pid = pid?;
        if !process::runs_above_us(pid) {
            return None;
        }
        let app = process::name(pid).unwrap_or_else(|| format!("process {pid}"));
        Some(self.body_error(
            id.to_owned(),
            ErrorBody {
                code: "TARGET_ELEVATED".to_owned(),
                message: format!(
                    "{app} runs as administrator. Windows does not let a normal process \
                     send it input or read it (UIPI), and drops such input without an \
                     error, so nothing was sent. Use it yourself, or start it without \
                     administrator rights."
                ),
                retryable: false,
                category: "permission".to_owned(),
            },
        ))
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
