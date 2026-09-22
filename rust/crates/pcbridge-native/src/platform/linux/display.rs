//! Mutter `GetCurrentState` transport.
//!
//! This module owns nothing but the wire: it asks `org.gnome.Mutter.DisplayConfig`
//! for the current state, turns the reply into the neutral `DisplayState`, and
//! hands it to `pcbridge_core::display::resolve`. Every rule -- which mode counts,
//! when the axes swap, how the table is ordered, how the topology id is built --
//! lives in core so that the Python host and this backend cannot drift apart.
//!
//! The cache is invalidated by Mutter's own `MonitorsChanged` signal rather than
//! by a timer, so a monitor that is plugged in mid-session is picked up on the
//! next snapshot instead of up to a cache lifetime later.

use std::collections::HashMap;
use std::sync::Mutex;
use std::time::Duration;

use async_io::Timer;
use futures_lite::{StreamExt, future};
use pcbridge_core::display::{
    DisplayError, DisplayState, LayoutMode, LogicalMonitor, Monitor, PhysicalMonitor,
};
use pcbridge_core::display::{DisplayMode, resolve, topology_id};
use zbus::proxy::SignalStream;
use zbus::zvariant::OwnedValue;
use zbus::{Connection, Proxy, connection::Builder};

const DESTINATION: &str = "org.gnome.Mutter.DisplayConfig";
const PATH: &str = "/org/gnome/Mutter/DisplayConfig";
const INTERFACE: &str = "org.gnome.Mutter.DisplayConfig";
/// Same ceiling as the desktop-state observations: a hung compositor must not
/// hold a protected request open.
const METHOD_TIMEOUT: Duration = Duration::from_millis(200);

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

/// Reads the display table and keeps it fresh from `MonitorsChanged`.
///
/// `Debug` is hand written: the signal stream is not `Debug`, and printing a
/// live D-Bus connection would be noise rather than information.
pub struct DisplayReader {
    connection: Connection,
    changes: Mutex<SignalStream<'static>>,
    cached: Mutex<Option<DisplaySnapshot>>,
}

impl std::fmt::Debug for DisplayReader {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("DisplayReader")
            .field("cached", &self.cached_snapshot().is_some())
            .finish_non_exhaustive()
    }
}

impl DisplayReader {
    pub fn connect() -> Result<Self, zbus::Error> {
        let connection =
            zbus::block_on(Builder::session()?.method_timeout(METHOD_TIMEOUT).build())?;
        let proxy = zbus::block_on(Proxy::new(&connection, DESTINATION, PATH, INTERFACE))?;
        let changes = zbus::block_on(proxy.receive_signal("MonitorsChanged"))?;
        Ok(Self {
            connection,
            changes: Mutex::new(changes),
            cached: Mutex::new(None),
        })
    }

    /// Drain pending `MonitorsChanged` signals; true when the layout moved.
    fn layout_changed(&self) -> bool {
        let mut stream = match self.changes.lock() {
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

        let wire: WireState = zbus::block_on(async {
            let proxy = Proxy::new(&self.connection, DESTINATION, PATH, INTERFACE).await?;
            proxy.call("GetCurrentState", &()).await
        })?;

        let monitors = resolve(&to_state(wire))?;
        let snapshot = DisplaySnapshot {
            topology_id: topology_id(&monitors),
            monitors,
        };
        if let Ok(mut cached) = self.cached.lock() {
            *cached = Some(snapshot.clone());
        }
        Ok(snapshot)
    }

    fn cached_snapshot(&self) -> Option<DisplaySnapshot> {
        self.cached.lock().ok().and_then(|cached| cached.clone())
    }

    pub fn invalidate(&self) {
        if let Ok(mut cached) = self.cached.lock() {
            *cached = None;
        }
    }
}
