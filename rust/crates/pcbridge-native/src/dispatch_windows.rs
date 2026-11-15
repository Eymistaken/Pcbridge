//! The protocol dispatcher on Windows (docs/native/protocol-v1.md).
//!
//! The same requests and answers as the Linux dispatcher, served by Win32
//! instead of AT-SPI, PipeWire, uinput and X11. It grows with the Windows
//! port's W6 stages; a method whose backend has not arrived yet answers
//! `BACKEND_UNAVAILABLE`, which the Python side reports as an unavailable
//! capability rather than a failure.

use std::path::{Path, PathBuf};

use pcbridge_core::{
    ErrorBody, Frame, PROTOCOL_MAJOR, PROTOCOL_MINOR, ProtocolError, RequestHeader, ResponseHeader,
};
use serde::Deserialize;
use serde_json::{Value, json};

use crate::platform::windows::lifecycle::Binding;
use crate::platform::windows::{capture, display};

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
        vec!["display.snapshot", "capture.on_demand"]
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
                unavailable("input.keyboard", "os.keyboard"),
                unavailable("input.pointer", "os.pointer"),
                unavailable("input.pointer_relative", "os.pointer"),
                unavailable("clipboard.read", "os.clipboard"),
                unavailable("clipboard.write", "os.clipboard"),
                unavailable("accessibility.read", "os.accessibility"),
                unavailable("accessibility.windows", "os.accessibility"),
                unavailable("accessibility.action", "os.accessibility"),
            ],
        })
    }
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

pub struct Dispatcher {
    mode: BackendMode,
    initialized: bool,
    negotiated_minor: Option<u16>,
    state_dir: Option<PathBuf>,
    binding: Option<Binding>,
    frame_sequence: u64,
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
            && request.method != "accessibility.set_text"
        {
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
            "cancel" => match serde_json::from_value::<CancelParams>(request.params) {
                Ok(params) if !params.target_id.is_empty() => self.success(
                    request.id,
                    json!({"target_id": params.target_id, "canceled": false}),
                    None,
                ),
                _ => self.error(
                    request.id,
                    "INVALID_PARAMS",
                    "invalid cancel parameters",
                    None,
                ),
            },
            "shutdown" => self.success(
                request.id,
                json!({"shutdown": true}),
                Some(CloseConnection::Shutdown),
            ),
            method if is_known(method) => self.error(
                request.id,
                "BACKEND_UNAVAILABLE",
                format!("'{method}' has no Windows backend yet"),
                None,
            ),
            method => self.error(
                request.id,
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
