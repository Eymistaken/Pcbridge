//! Display table transports: Mutter `GetCurrentState` and KScreen.
//!
//! This module owns nothing but the wire: it asks `org.gnome.Mutter.DisplayConfig`
//! (GNOME) or `kscreen-doctor -j` (KDE Plasma) for the current state, turns the
//! reply into the neutral `DisplayState`, and hands it to
//! `pcbridge_core::display::resolve`. Every rule -- which mode counts,
//! when the axes swap, how the table is ordered, how the topology id is built --
//! lives in core so that the Python host and this backend cannot drift apart.
//!
//! On GNOME the cache is invalidated by Mutter's own `MonitorsChanged` signal
//! rather than by a timer, so a monitor that is plugged in mid-session is picked
//! up on the next snapshot instead of up to a cache lifetime later. KScreen has
//! no such signal on this path; its table is kept for `KSCREEN_CACHE`, the same
//! two seconds the Python host keeps it.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use async_io::Timer;
use futures_lite::{StreamExt, future};
use pcbridge_core::display::{
    DisplayError, DisplayState, LayoutMode, LogicalMonitor, Monitor, PhysicalMonitor,
};
use pcbridge_core::display::{DisplayMode, resolve, topology_id};
use serde_json::Value;
use zbus::proxy::SignalStream;
use zbus::zvariant::OwnedValue;
use zbus::{Connection, Proxy, connection::Builder};

use super::desktop::{DesktopKind, hyprland_socket};
use super::hyprland::{ReadQuery, query};

const DESTINATION: &str = "org.gnome.Mutter.DisplayConfig";
const PATH: &str = "/org/gnome/Mutter/DisplayConfig";
const INTERFACE: &str = "org.gnome.Mutter.DisplayConfig";
/// Same ceiling as the desktop-state observations: a hung compositor must not
/// hold a protected request open.
const METHOD_TIMEOUT: Duration = Duration::from_millis(200);
/// How long a KScreen table is reused (`kscreen-doctor -j` takes 18-32 ms).
const KSCREEN_CACHE: Duration = Duration::from_secs(2);

type Props = HashMap<String, OwnedValue>;
/// `(id, width, height, refresh, preferred_scale, supported_scales, props)`
type WireMode = (String, i32, i32, f64, f64, Vec<f64>, Props);
/// `(connector, vendor, product, serial)`
type WireIdentity = (String, String, String, String);
/// `(identity, modes, props)`
type WireMonitor = (WireIdentity, Vec<WireMode>, Props);
/// `(x, y, scale, transform, primary, monitors, props)`
type WireLogical = (i32, i32, f64, u32, bool, Vec<WireIdentity>, Props);
/// `(serial, monitors, logical monitors, properties)`
type WireState = (u32, Vec<WireMonitor>, Vec<WireLogical>, Props);

/// A resolved table plus the identity callers compare against.
#[derive(Debug, Clone)]
pub struct DisplaySnapshot {
    pub monitors: Vec<Monitor>,
    pub topology_id: String,
}

#[derive(Debug, thiserror::Error)]
pub enum SnapshotError {
    #[error("display config unavailable: {0}")]
    Transport(#[from] zbus::Error),
    #[error("KScreen unavailable: {0}")]
    KScreen(String),
    #[error("Hyprland display IPC unavailable: {0}")]
    Hyprland(String),
    #[error(transparent)]
    Mapping(#[from] DisplayError),
}

fn prop_bool(props: &Props, key: &str) -> bool {
    props
        .get(key)
        .and_then(|value| bool::try_from(value.clone()).ok())
        .unwrap_or(false)
}

fn prop_string(props: &Props, key: &str) -> String {
    props
        .get(key)
        .and_then(|value| String::try_from(value.clone()).ok())
        .unwrap_or_default()
}

fn prop_u32(props: &Props, key: &str) -> Option<u32> {
    props
        .get(key)
        .and_then(|value| u32::try_from(value.clone()).ok())
}

/// Wire reply to the neutral state. No rules here on purpose.
fn to_state(wire: WireState) -> DisplayState {
    let (_serial, monitors, logical, props) = wire;
    // 2 = physical (MetaLogicalMonitorLayoutMode); 1 and a missing property
    // are the logical mode.
    let layout_mode = match prop_u32(&props, "layout-mode") {
        Some(2) => LayoutMode::Physical,
        _ => LayoutMode::Logical,
    };
    DisplayState {
        layout_mode,
        physical: monitors
            .into_iter()
            .map(|(identity, modes, props)| {
                let (connector, vendor, product, serial) = identity;
                let display_name = prop_string(&props, "display-name");
                PhysicalMonitor {
                    connector,
                    vendor,
                    product,
                    serial,
                    display_name,
                    modes: modes
                        .into_iter()
                        .map(
                            |(_id, width, height, _refresh, _pref, _scales, mode_props)| {
                                DisplayMode {
                                    width: width.max(0) as u32,
                                    height: height.max(0) as u32,
                                    is_current: prop_bool(&mode_props, "is-current"),
                                }
                            },
                        )
                        .collect(),
                }
            })
            .collect(),
        logical: logical
            .into_iter()
            .map(
                |(x, y, scale, transform, primary, members, _props)| LogicalMonitor {
                    x,
                    y,
                    scale,
                    transform,
                    primary,
                    connectors: members
                        .into_iter()
                        .map(|(connector, ..)| connector)
                        .collect(),
                },
            )
            .collect(),
    }
}

/// KScreen's rotation flag to Mutter's transform code (the neutral state's).
fn kscreen_transform(rotation: u64) -> Option<u32> {
    match rotation {
        1 => Some(0),
        2 => Some(1),
        4 => Some(2),
        8 => Some(3),
        16 => Some(4),
        32 => Some(5),
        64 => Some(6),
        128 => Some(7),
        _ => None,
    }
}

fn json_u32(value: &Value) -> u32 {
    value
        .as_u64()
        .and_then(|number| u32::try_from(number).ok())
        .unwrap_or(0)
}

fn json_i32(value: &Value) -> i32 {
    value
        .as_i64()
        .and_then(|number| i32::try_from(number).ok())
        .unwrap_or(0)
}

/// `kscreen-doctor -j` to the neutral state; `monitors._kscreen_state` in
/// Python does the same and `kscreen_cases.json` pins both. No rules here.
pub fn kscreen_state(data: &Value) -> Result<DisplayState, String> {
    let mut physical = Vec::new();
    let mut logical = Vec::new();
    for output in data["outputs"].as_array().into_iter().flatten() {
        if output["connected"].as_bool() != Some(true) {
            continue;
        }
        let name = output["name"].as_str().unwrap_or_default().to_owned();
        let current = match &output["currentModeId"] {
            Value::String(id) => id.clone(),
            other => other.to_string(),
        };
        let modes = output["modes"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|mode| {
                let id = match &mode["id"] {
                    Value::String(id) => id.clone(),
                    other => other.to_string(),
                };
                DisplayMode {
                    width: json_u32(&mode["size"]["width"]),
                    height: json_u32(&mode["size"]["height"]),
                    is_current: id == current,
                }
            })
            .collect();
        physical.push(PhysicalMonitor {
            connector: name.clone(),
            vendor: String::new(),
            product: String::new(),
            serial: String::new(),
            display_name: name.clone(),
            modes,
        });
        if output["enabled"].as_bool() != Some(true) {
            continue;
        }
        let rotation = output["rotation"].as_u64().unwrap_or(1);
        let transform = kscreen_transform(rotation)
            .ok_or_else(|| format!("unknown KScreen rotation {rotation} for {name}"))?;
        logical.push(LogicalMonitor {
            x: json_i32(&output["pos"]["x"]),
            y: json_i32(&output["pos"]["y"]),
            scale: output["scale"].as_f64().unwrap_or(0.0),
            transform,
            primary: output["priority"].as_u64() == Some(1),
            connectors: vec![name],
        });
    }
    Ok(DisplayState {
        layout_mode: LayoutMode::Logical,
        physical,
        logical,
    })
}

fn read_kscreen() -> Result<DisplayState, SnapshotError> {
    let output = Command::new("kscreen-doctor")
        .arg("-j")
        .output()
        .map_err(|error| SnapshotError::KScreen(format!("kscreen-doctor did not run: {error}")))?;
    if !output.status.success() {
        return Err(SnapshotError::KScreen(
            String::from_utf8_lossy(&output.stderr).trim().to_owned(),
        ));
    }
    let data: Value = serde_json::from_slice(&output.stdout)
        .map_err(|error| SnapshotError::KScreen(format!("unreadable JSON: {error}")))?;
    kscreen_state(&data).map_err(SnapshotError::KScreen)
}

/// Hyprland `j/monitors` to the same neutral state as Python's adapter.
/// `width` and `height` are raw mode pixels even at fractional scale and
/// rotation; the shared resolver divides and swaps axes exactly once.
pub fn hyprland_state(data: &Value) -> Result<DisplayState, String> {
    let outputs = data.as_array().ok_or("monitors reply is not an array")?;
    let mut names = HashSet::new();
    let mut physical = Vec::new();
    let mut logical = Vec::new();
    for output in outputs {
        if output["disabled"].as_bool() == Some(true) {
            continue;
        }
        let name = output["name"]
            .as_str()
            .filter(|name| !name.is_empty())
            .ok_or("monitor name is missing")?;
        if !names.insert(name) {
            return Err(format!("duplicate Hyprland output name {name}"));
        }
        if let Some(mirror_value) = output.get("mirrorOf") {
            let mirror = mirror_value
                .as_str()
                .ok_or_else(|| format!("invalid mirror state for {name}"))?;
            if mirror != "none" && !mirror.is_empty() {
                return Err(format!(
                    "mirrored Hyprland output {name} needs explicit mapping"
                ));
            }
        }
        let dimension = |field: &str| -> Result<u32, String> {
            let value = output[field]
                .as_u64()
                .and_then(|value| u32::try_from(value).ok())
                .filter(|value| *value > 0);
            value.ok_or_else(|| format!("invalid {field} for {name}"))
        };
        let position = |field: &str| -> Result<i32, String> {
            output[field]
                .as_i64()
                .and_then(|value| i32::try_from(value).ok())
                .ok_or_else(|| format!("invalid {field} for {name}"))
        };
        let width = dimension("width")?;
        let height = dimension("height")?;
        let x = position("x")?;
        let y = position("y")?;
        let scale = output["scale"]
            .as_f64()
            .filter(|scale| scale.is_finite() && *scale > 0.0)
            .ok_or_else(|| format!("invalid scale for {name}"))?;
        let transform = output["transform"]
            .as_u64()
            .and_then(|value| u32::try_from(value).ok())
            .filter(|value| *value <= 7)
            .ok_or_else(|| format!("invalid transform for {name}"))?;
        physical.push(PhysicalMonitor {
            connector: name.to_owned(),
            vendor: output["make"].as_str().unwrap_or_default().to_owned(),
            product: output["model"].as_str().unwrap_or_default().to_owned(),
            serial: output["serial"].as_str().unwrap_or_default().to_owned(),
            display_name: output["description"].as_str().unwrap_or(name).to_owned(),
            modes: vec![DisplayMode {
                width,
                height,
                is_current: true,
            }],
        });
        logical.push(LogicalMonitor {
            x,
            y,
            scale,
            transform,
            primary: output["focused"].as_bool() == Some(true),
            connectors: vec![name.to_owned()],
        });
    }
    if logical.iter().filter(|monitor| monitor.primary).count() > 1 {
        return Err("Hyprland reported multiple focused outputs".into());
    }
    if !logical.is_empty() && !logical.iter().any(|monitor| monitor.primary) {
        logical[0].primary = true;
    }
    Ok(DisplayState {
        layout_mode: LayoutMode::Logical,
        physical,
        logical,
    })
}

fn read_hyprland(socket_path: &Path) -> Result<DisplayState, SnapshotError> {
    let data = query(socket_path, ReadQuery::Monitors).map_err(SnapshotError::Hyprland)?;
    hyprland_state(&data).map_err(SnapshotError::Hyprland)
}

enum Source {
    Mutter {
        connection: Connection,
        changes: Box<Mutex<SignalStream<'static>>>,
    },
    KScreen,
    Hyprland(PathBuf),
}

/// Reads the display table and keeps it fresh (GNOME: `MonitorsChanged`;
/// Plasma: a short cache).
///
/// `Debug` is hand written: the signal stream is not `Debug`, and printing a
/// live D-Bus connection would be noise rather than information.
pub struct DisplayReader {
    source: Source,
    cached: Mutex<Option<(Instant, DisplaySnapshot)>>,
}

impl std::fmt::Debug for DisplayReader {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("DisplayReader")
            .field("kscreen", &matches!(self.source, Source::KScreen))
            .field("cached", &self.cached_snapshot().is_some())
            .finish_non_exhaustive()
    }
}

impl DisplayReader {
    /// The reader for this session's desktop.
    pub fn connect() -> Result<Self, zbus::Error> {
        match DesktopKind::detect() {
            DesktopKind::Kde => Ok(Self {
                source: Source::KScreen,
                cached: Mutex::new(None),
            }),
            DesktopKind::Gnome => Self::connect_mutter(),
            DesktopKind::Hyprland => Ok(Self {
                source: Source::Hyprland(hyprland_socket().ok_or_else(|| {
                    zbus::Error::Failure("Hyprland session socket unavailable".into())
                })?),
                cached: Mutex::new(None),
            }),
            DesktopKind::Unknown => Err(zbus::Error::Failure(
                "desktop display backend unavailable".into(),
            )),
        }
    }

    pub fn connect_mutter() -> Result<Self, zbus::Error> {
        let connection =
            zbus::block_on(Builder::session()?.method_timeout(METHOD_TIMEOUT).build())?;
        let proxy = zbus::block_on(Proxy::new(&connection, DESTINATION, PATH, INTERFACE))?;
        let changes = zbus::block_on(proxy.receive_signal("MonitorsChanged"))?;
        Ok(Self {
            source: Source::Mutter {
                connection,
                changes: Box::new(Mutex::new(changes)),
            },
            cached: Mutex::new(None),
        })
    }

    /// Drain pending `MonitorsChanged` signals; true when the layout moved.
    /// KScreen: true once the cached table is older than `KSCREEN_CACHE`.
    fn layout_changed(&self) -> bool {
        let changes = match &self.source {
            Source::Mutter { changes, .. } => changes,
            Source::KScreen | Source::Hyprland(_) => {
                return self
                    .cached
                    .lock()
                    .ok()
                    .and_then(|cached| cached.as_ref().map(|(at, _)| at.elapsed()))
                    .is_some_and(|age| age >= KSCREEN_CACHE);
            }
        };
        let mut stream = match changes.lock() {
            Ok(stream) => stream,
            Err(poisoned) => poisoned.into_inner(),
        };
        // Bekleyen sinyalleri bloklamadan bosalt: `or` once ilk gelecegi
        // yokluyor, yani sinyal varsa hemen doner; yoksa sifir sureli zamanlayici
        // kazanir. Ayni kalip `desktop_state.rs`te de kullaniliyor.
        let mut changed = false;
        loop {
            let pending = zbus::block_on(future::or(
                async { stream.next().await.map(|_| ()) },
                async {
                    Timer::after(Duration::ZERO).await;
                    None
                },
            ));
            if pending.is_none() {
                break;
            }
            changed = true;
        }
        changed
    }

    /// The current table, refused rather than guessed when it cannot be mapped.
    pub fn snapshot(&self) -> Result<DisplaySnapshot, SnapshotError> {
        if self.layout_changed() {
            self.invalidate();
        } else if let Some(cached) = self.cached_snapshot() {
            return Ok(cached);
        }

        let state = match &self.source {
            Source::Mutter { connection, .. } => {
                let wire: WireState = zbus::block_on(async {
                    let proxy = Proxy::new(connection, DESTINATION, PATH, INTERFACE).await?;
                    proxy.call("GetCurrentState", &()).await
                })?;
                to_state(wire)
            }
            Source::KScreen => read_kscreen()?,
            Source::Hyprland(socket) => read_hyprland(socket)?,
        };

        let monitors = resolve(&state)?;
        let snapshot = DisplaySnapshot {
            topology_id: topology_id(&monitors),
            monitors,
        };
        if let Ok(mut cached) = self.cached.lock() {
            *cached = Some((Instant::now(), snapshot.clone()));
        }
        Ok(snapshot)
    }

    fn cached_snapshot(&self) -> Option<DisplaySnapshot> {
        self.cached
            .lock()
            .ok()
            .and_then(|cached| cached.as_ref().map(|(_, snapshot)| snapshot.clone()))
    }

    pub fn invalidate(&self) {
        if let Ok(mut cached) = self.cached.lock() {
            *cached = None;
        }
    }
}
