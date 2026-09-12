use std::path::Path;
#[cfg(feature = "test-harness")]
use std::sync::Arc;

use pcbridge_core::{
    ErrorBody, Frame, PROTOCOL_MAJOR, PROTOCOL_MINOR, ProtocolError, RequestHeader, ResponseHeader,
};
use serde::Deserialize;
use serde_json::{Value, json};

#[cfg(feature = "test-harness")]
use crate::lifecycle::LeaseFailure;
use crate::lifecycle::Lifecycle;
use crate::platform::linux::capture::{CaptureError, NativeCapture, NativeCaptureError};
#[cfg(feature = "test-harness")]
use crate::platform::linux::desktop_state::DeterministicDesktopState;
use crate::platform::linux::display::DisplayReader;
use crate::platform::linux::display::DisplaySnapshot;
use crate::platform::linux::session::SessionFailure;

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
            Self::Production { .. } => vec!["display.snapshot", "capture.on_demand"],
            #[cfg(feature = "test-harness")]
            Self::DeterministicTest => vec!["test.fixture", "test.lease"],
        }
    }

    fn capabilities(&self) -> Value {
        match self {
            Self::Production { .. } => json!({
                "backend": "linux.mutter.pipewire",
                "capabilities": [
                    {
                        "name": "capture.monitor",
                        "status": "supported",
                        "permission_scope": "os.capture",
                    }
                ],
            }),
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

        if !frame.binary.is_empty() {
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
            "cancel" => match parse_cancel(request.params) {
                Ok(target_id) => self.success(
                    request.id,
                    json!({"target_id": target_id, "canceled": false}),
                    None,
                ),
                Err(message) => self.error(request.id, "INVALID_PARAMS", message, None),
            },
            "shutdown" => self.success(
                request.id,
                json!({"shutdown": true}),
                Some(CloseConnection::Shutdown),
            ),
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
        self.initialized = true;
        self.negotiated_minor = Some(PROTOCOL_MINOR);
        self.success(
            request.id,
            json!({
                "instance_id": self.mode.instance_id(),
                "native_version": env!("CARGO_PKG_VERSION"),
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
        assert_eq!(mode.capabilities()["backend"], "linux.mutter.pipewire");
        assert_eq!(
            mode.features(),
            vec!["display.snapshot", "capture.on_demand"]
        );
        assert_eq!(
            mode.capabilities()["capabilities"][0]["name"],
            "capture.monitor"
        );
        assert_eq!(
            mode.capabilities()["capabilities"][0]["status"],
            "supported"
        );
    }
}
