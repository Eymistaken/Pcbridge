use std::path::Path;

use pcbridge_core::{
    ErrorBody, Frame, PROTOCOL_MAJOR, PROTOCOL_MINOR, ProtocolError, RequestHeader, ResponseHeader,
};
use serde::Deserialize;
use serde_json::{Value, json};

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
            Self::Production { .. } => Vec::new(),
            #[cfg(feature = "test-harness")]
            Self::DeterministicTest => vec!["test.fixture"],
        }
    }

    fn capabilities(&self) -> Value {
        match self {
            Self::Production { .. } => json!({
                "backend": "protocol-only",
                "capabilities": [],
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
    pub close: Option<CloseConnection>,
}

#[derive(Debug)]
pub struct Dispatcher {
    mode: BackendMode,
    initialized: bool,
    negotiated_minor: Option<u16>,
}

impl Dispatcher {
    #[must_use]
    pub const fn new(mode: BackendMode) -> Self {
        Self {
            mode,
            initialized: false,
            negotiated_minor: None,
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

        self.initialized = true;
        self.negotiated_minor = Some(PROTOCOL_MINOR);
        self.success(
            request.id,
            json!({
                "instance_id": self.mode.instance_id(),
                "native_version": env!("CARGO_PKG_VERSION"),
                "platform": self.mode.platform(),
                "features": self.mode.features(),
            }),
            None,
        )
    }

    fn success(
        &self,
        id: String,
        result: Value,
        close: Option<CloseConnection>,
    ) -> DispatchOutcome {
        DispatchOutcome {
            response: ResponseHeader::success(id, result),
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
            close,
        }
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
        assert_eq!(mode.capabilities()["backend"], "protocol-only");
        assert!(mode.features().is_empty());
    }
}
