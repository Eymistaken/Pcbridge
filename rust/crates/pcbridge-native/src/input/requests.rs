//! The parameters of the `input.*` requests and their checks, shared by
//! the Linux and Windows dispatchers (docs/native/protocol-v1.md).

use std::time::Duration;

use serde::Deserialize;
use serde::de::DeserializeOwned;
use serde_json::Value;

use super::PointerConfig;

#[derive(Debug, Deserialize)]
pub(crate) struct KeyboardGrantParams {
    pub(crate) grant_id: String,
    pub(crate) revoke_epoch: u64,
    pub(crate) hold_max_seconds: u64,
}

#[derive(Debug, Deserialize)]
pub(crate) struct KeyboardActionParams {
    #[serde(flatten)]
    pub(crate) grant: KeyboardGrantParams,
    pub(crate) combo: String,
}

#[derive(Debug, Deserialize)]
pub(crate) struct PointerGrantParams {
    pub(crate) grant_id: String,
    pub(crate) revoke_epoch: u64,
    pub(crate) hold_max_seconds: u64,
    pub(crate) topology_id: String,
    pub(crate) pointer_speed: f64,
    pub(crate) pointer_max_ms: f64,
}

/// The envelope for the SECOND, relative device.
///
/// Deliberately narrow. `topology_id` is absent because a relative device has
/// no ABS range: the screen layout cannot invalidate it, and accepting the
/// field only to ignore it would tell the next reader that the topology was
/// checked. `hold_max_seconds` is absent because this device never presses a
/// button, so nothing can be left held.
#[derive(Debug, Deserialize)]
pub(crate) struct RelativeGrantParams {
    pub(crate) grant_id: String,
    pub(crate) revoke_epoch: u64,
}

#[derive(Debug, Deserialize)]
pub(crate) struct RelativeMoveParams {
    #[serde(flatten)]
    pub(crate) grant: RelativeGrantParams,
    pub(crate) dx: i32,
    pub(crate) dy: i32,
}

/// Which devices `input.pointer.ensure` should open.
///
/// Both default to the old behavior, so a helper talking to an older client
/// that sends only the grant envelope opens the absolute pointer exactly as
/// before.
#[derive(Debug, Deserialize)]
pub(crate) struct PointerEnsureParams {
    #[serde(flatten)]
    pub(crate) grant: PointerGrantParams,
    #[serde(default = "default_true")]
    pub(crate) pointer: bool,
    #[serde(default)]
    pub(crate) relative: bool,
}

#[derive(Debug, Deserialize)]
pub(crate) struct PointerMoveParams {
    #[serde(flatten)]
    pub(crate) grant: PointerGrantParams,
    pub(crate) x: i32,
    pub(crate) y: i32,
    #[serde(default)]
    pub(crate) smooth: Option<bool>,
}

#[derive(Debug, Deserialize)]
pub(crate) struct PointerClickParams {
    #[serde(flatten)]
    pub(crate) grant: PointerGrantParams,
    pub(crate) button: String,
    pub(crate) count: u8,
    /// Each press's duration. Absent means `DEFAULT_CLICK_HOLD`; pcbridge's
    /// Python side always sends it, resolved from `[desktop] click_hold_ms`.
    #[serde(default)]
    pub(crate) hold_ms: Option<u64>,
}

#[derive(Debug, Deserialize)]
pub(crate) struct PointerDragParams {
    #[serde(flatten)]
    pub(crate) grant: PointerGrantParams,
    pub(crate) x1: i32,
    pub(crate) y1: i32,
    pub(crate) x2: i32,
    pub(crate) y2: i32,
    pub(crate) button: String,
}

#[derive(Debug, Deserialize)]
pub(crate) struct PointerScrollParams {
    #[serde(flatten)]
    pub(crate) grant: PointerGrantParams,
    pub(crate) amount: i32,
    #[serde(default)]
    pub(crate) horizontal: bool,
}

#[derive(Debug, Deserialize)]
pub(crate) struct PointerButtonParams {
    #[serde(flatten)]
    pub(crate) grant: PointerGrantParams,
    pub(crate) button: String,
}

impl KeyboardGrantParams {
    pub(crate) fn validate(&self) -> Result<(), &'static str> {
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
    pub(crate) fn validate(&self) -> Result<(), &'static str> {
        self.grant.validate()?;
        if self.combo.is_empty() || self.combo.len() > 256 {
            return Err("combo must be nonempty and at most 256 bytes");
        }
        Ok(())
    }
}

impl RelativeGrantParams {
    pub(crate) fn validate(&self) -> Result<(), &'static str> {
        const MAX_ID_BYTES: usize = 256;
        if self.grant_id.is_empty() || self.grant_id.len() > MAX_ID_BYTES {
            return Err("grant_id must be nonempty and bounded");
        }
        Ok(())
    }
}

impl PointerGrantParams {
    pub(crate) fn validate(&self) -> Result<(), &'static str> {
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

    pub(crate) fn config(&self) -> PointerConfig {
        PointerConfig {
            speed: self.pointer_speed,
            max_ms: self.pointer_max_ms,
            step: POINTER_STEP,
            hold_max: Duration::from_secs(self.hold_max_seconds),
            drag_min_steps: 10,
        }
    }
}

/// Gap between the chunks of one relative nudge. Same 8 ms the absolute
/// pointer walks its path with; real mice report at 125 Hz.
pub(crate) const POINTER_STEP: Duration = Duration::from_millis(8);

pub(crate) const POINTER_GRANT_FIELDS: &[&str] = &[
    "grant_id",
    "revoke_epoch",
    "hold_max_seconds",
    "topology_id",
    "pointer_speed",
    "pointer_max_ms",
];

pub(crate) const RELATIVE_GRANT_FIELDS: &[&str] = &["grant_id", "revoke_epoch"];

pub(crate) fn parse_relative_params<T: DeserializeOwned>(
    params: Value,
    action_fields: &[&str],
) -> Result<T, String> {
    let object = params
        .as_object()
        .ok_or_else(|| "relative pointer parameters must be an object".to_owned())?;
    if let Some(field) = object.keys().find(|field| {
        !RELATIVE_GRANT_FIELDS.contains(&field.as_str()) && !action_fields.contains(&field.as_str())
    }) {
        return Err(format!("unknown relative pointer parameter '{field}'"));
    }
    serde_json::from_value(params).map_err(|error| error.to_string())
}

pub(crate) fn parse_pointer_params<T: DeserializeOwned>(
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

const fn default_true() -> bool {
    true
}
