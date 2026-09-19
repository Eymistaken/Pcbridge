//! Monitor table resolution: the single source of the coordinate space.
//!
//! A pcbridge desktop coordinate is always in the global canvas: top-left is
//! (0, 0). This module turns Mutter's `GetCurrentState` reply into the ordered
//! monitor table that both the Python host and the native backend must agree
//! on, and it does so without touching D-Bus, so the rules can be tested from a
//! fixture. `platform::linux::display` owns the transport.
//!
//! The canvas and the compositor's own space are separate (Task 7.1). A
//! compositor may place a monitor at a negative origin; the table is
//! translated once, here, so the canvas always starts at (0, 0), and each
//! monitor keeps the platform position it was reported at. A negative canvas
//! coordinate could not be reached by the absolute pointer axis and would
//! crop outside the captured image.
//!
//! The rules mirror `pcbridge/desktop/monitors.py` exactly and both sides are
//! pinned by `tests/fixtures/native/display_state_cases.json`. Nothing is
//! guessed: a monitor with no current mode, an unknown connector, an empty
//! connector list or a non-positive scale is refused rather than approximated.
//! Picking "the first mode" or "the first monitor" is how a capture silently
//! lands on the wrong screen.

use serde::Deserialize;
use thiserror::Error;

/// Version marker for the canonical topology string. Bump it whenever the
/// layout of that string changes, so a stale cached id cannot compare equal.
pub const TOPOLOGY_VERSION: &str = "v1";

/// Mutter transform codes that swap the mode axes: 90 and 270 degrees, plus
/// their mirrored twins.
const SWAPS_AXES: [u32; 4] = [1, 3, 5, 7];

#[derive(Debug, Clone, Deserialize)]
pub struct DisplayMode {
    pub width: u32,
    pub height: u32,
    #[serde(default)]
    pub is_current: bool,
}

#[derive(Debug, Clone, Deserialize)]
pub struct PhysicalMonitor {
    pub connector: String,
    #[serde(default)]
    pub vendor: String,
    #[serde(default)]
    pub product: String,
    #[serde(default)]
    pub serial: String,
    #[serde(default)]
    pub display_name: String,
    #[serde(default)]
    pub modes: Vec<DisplayMode>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct LogicalMonitor {
    pub x: i32,
    pub y: i32,
    pub scale: f64,
    #[serde(default)]
    pub transform: u32,
    #[serde(default)]
    pub primary: bool,
    #[serde(default)]
    pub connectors: Vec<String>,
}

/// The meaning of a `GetCurrentState` reply, free of its wire encoding.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct DisplayState {
    #[serde(default)]
    pub physical: Vec<PhysicalMonitor>,
    #[serde(default)]
    pub logical: Vec<LogicalMonitor>,
}

/// One logical monitor, already ordered and numbered for public use.
#[derive(Debug, Clone, PartialEq)]
pub struct Monitor {
    /// 1-based, ordered by `(x, y)`. Deliberately not primary-first: on the
    /// target machine the primary monitor is the right-hand one, and calling it
    /// "monitor 1" would contradict what the user sees.
    pub index: u32,
    pub connector: String,
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
    pub scale: f64,
    pub primary: bool,
    pub name: String,
    /// 0 = normal, 1 = 90, 2 = 180, 3 = 270, 4-7 mirrored. `width`/`height` are
    /// already swapped where that applies; the code is kept because a half turn
    /// leaves the size alone while changing how coordinates map.
    pub transform: u32,
    /// Physical identity. The connector name is NOT stable: on the target
    /// machine it went from DP-1/DP-2 to DP-3/DP-4 with the geometry untouched
    /// (measured 2026-09-12 from both Mutter and `xrandr --listmonitors`).
    pub serial: String,
    /// Where the compositor itself put this monitor. `x`/`y` are the canvas
    /// position, which always starts at (0, 0); these two may be negative.
    pub platform_x: i32,
    pub platform_y: i32,
}

impl Monitor {
    /// The monitor's size in raw pixels: the logical size times its scale.
    ///
    /// Equal to the logical size at scale 1. The rounding rule is the table's
    /// own, so a fractional scale cannot drift by a pixel between the two
    /// languages.
    pub fn source_pixel_size(&self) -> (u32, u32) {
        (
            round_half_away(f64::from(self.width) * self.scale) as u32,
            round_half_away(f64::from(self.height) * self.scale) as u32,
        )
    }
}

/// Every variant maps to `DISPLAY_MAPPING_UNKNOWN` at the host boundary: the
/// layout could not be established, and acting on a guess would put clicks on
/// the wrong screen.
#[derive(Debug, Error, PartialEq)]
pub enum DisplayError {
    #[error("no logical monitor was reported")]
    NoLogicalMonitors,
    #[error("logical monitor reported no connector; its current mode is unknowable")]
    NoConnector,
    #[error("no current mode for connector {0}")]
    NoCurrentMode(String),
    #[error("invalid scale {scale} for connector {connector}")]
    InvalidScale { connector: String, scale: f64 },
    #[error("invalid logical size {width}x{height} for connector {connector}")]
    InvalidSize {
        connector: String,
        width: i64,
        height: i64,
    },
}

/// Round half away from zero: 960.5 becomes 961.
///
/// Spelled out on purpose. Rust's `f64::round` already does this, but Python's
/// built-in `round` is banker's rounding and would answer 960, so the rule is
/// written explicitly on both sides instead of inherited from whichever
/// standard library happens to be running. Mutter's own behavior at the half
/// boundary is UNMEASURED: the target machine runs both monitors at scale 1.0,
/// so the division is always exact there.
fn round_half_away(value: f64) -> f64 {
    value.round()
}

/// Resolve the ordered monitor table, or refuse.
pub fn resolve(state: &DisplayState) -> Result<Vec<Monitor>, DisplayError> {
    let mut monitors: Vec<Monitor> = Vec::with_capacity(state.logical.len());

    for logical in &state.logical {
        let connector = logical
            .connectors
            .first()
            .filter(|name| !name.is_empty())
            .ok_or(DisplayError::NoConnector)?;

        let physical = state
            .physical
            .iter()
            .find(|entry| &entry.connector == connector)
            .ok_or_else(|| DisplayError::NoCurrentMode(connector.clone()))?;

        let mode = physical
            .modes
            .iter()
            .find(|mode| mode.is_current)
            .ok_or_else(|| DisplayError::NoCurrentMode(connector.clone()))?;

        if !(logical.scale.is_finite() && logical.scale > 0.0) {
            return Err(DisplayError::InvalidScale {
                connector: connector.clone(),
                scale: logical.scale,
            });
        }

        let (mode_width, mode_height) = if SWAPS_AXES.contains(&logical.transform) {
            (mode.height, mode.width)
        } else {
            (mode.width, mode.height)
        };

        let width = round_half_away(f64::from(mode_width) / logical.scale) as i64;
        let height = round_half_away(f64::from(mode_height) / logical.scale) as i64;
        if width <= 0 || height <= 0 {
            return Err(DisplayError::InvalidSize {
                connector: connector.clone(),
                width,
                height,
            });
        }

        let name = if physical.display_name.is_empty() {
            physical.product.clone()
        } else {
            physical.display_name.clone()
        };

        monitors.push(Monitor {
            index: 0, // assigned after ordering
            connector: connector.clone(),
            x: logical.x,
            y: logical.y,
            width: width as u32,
            height: height as u32,
            scale: logical.scale,
            primary: logical.primary,
            name,
            transform: logical.transform,
            serial: physical.serial.clone(),
            platform_x: logical.x,
            platform_y: logical.y,
        });
    }

    if monitors.is_empty() {
        return Err(DisplayError::NoLogicalMonitors);
    }

    // The canvas starts at (0, 0) whatever the compositor reported (Task 7.1).
    // One translation, here, for the same reason the coordinate conversion
    // lives in one place: a second one somewhere else would be forgotten.
    let left = monitors.iter().map(|monitor| monitor.x).min().unwrap_or(0);
    let top = monitors.iter().map(|monitor| monitor.y).min().unwrap_or(0);
    for monitor in &mut monitors {
        monitor.x -= left;
        monitor.y -= top;
    }

    monitors.sort_by_key(|monitor| (monitor.x, monitor.y));
    for (position, monitor) in monitors.iter_mut().enumerate() {
        monitor.index = position as u32 + 1;
    }
    Ok(monitors)
}

/// Stable identity of the layout, used to ask whether a cached snapshot or an
/// open capture session still describes reality.
///
/// A canonical string rather than a hash: no collisions, readable in a log, and
/// comparable across the two languages byte for byte. The connector name is
/// deliberately excluded, because it demonstrably drifts while nothing moves.
pub fn topology_id(monitors: &[Monitor]) -> String {
    let mut out = String::from(TOPOLOGY_VERSION);
    for monitor in monitors {
        out.push('|');
        out.push_str(&format!(
            "{},{},{},{},{:.4},{},{}",
            monitor.x,
            monitor.y,
            monitor.width,
            monitor.height,
            monitor.scale,
            monitor.transform,
            u8::from(monitor.primary),
        ));
    }
    out
}

/// Width and height of the global canvas the monitors span.
pub fn canvas_size(monitors: &[Monitor]) -> (u32, u32) {
    let left = monitors.iter().map(|monitor| monitor.x).min().unwrap_or(0);
    let top = monitors.iter().map(|monitor| monitor.y).min().unwrap_or(0);
    let right = monitors
        .iter()
        .map(|monitor| monitor.x + monitor.width as i32)
        .max()
        .unwrap_or(0);
    let bottom = monitors
        .iter()
        .map(|monitor| monitor.y + monitor.height as i32)
        .max()
        .unwrap_or(0);
    ((right - left).max(0) as u32, (bottom - top).max(0) as u32)
}

/// Where the canvas origin sits in the compositor's own space.
pub fn platform_origin(monitors: &[Monitor]) -> (i32, i32) {
    (
        monitors
            .iter()
            .map(|monitor| monitor.platform_x)
            .min()
            .unwrap_or(0),
        monitors
            .iter()
            .map(|monitor| monitor.platform_y)
            .min()
            .unwrap_or(0),
    )
}
