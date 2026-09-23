//! Typed screen-lock and user-activity observations, from GNOME or KDE Plasma.
//!
//! GNOME answers on `org.gnome.ScreenSaver` (lock) and Mutter's IdleMonitor
//! (idle time in ms). On Plasma, KWin answers the lock on the freedesktop
//! `org.freedesktop.ScreenSaver`, but not the idle time: that comes from the
//! `idle-watch` record (see `idle.rs` and docs/dev/measured-facts.md).

use std::sync::Mutex;
use std::thread;
use std::time::{Duration, SystemTime};

use async_io::Timer;
use futures_lite::{StreamExt, future};
use zbus::{Connection, Message, Proxy, connection::Builder, proxy::SignalStream};

use super::desktop::DesktopKind;
use super::idle;

/// Where one desktop answers the lock and idle questions.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct StateEndpoints {
    pub lock_destination: &'static str,
    pub lock_path: &'static str,
    pub lock_interface: &'static str,
    pub idle: IdleSource,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum IdleSource {
    /// A D-Bus method that returns the idle time in ms as `t`.
    Dbus {
        destination: &'static str,
        path: &'static str,
        interface: &'static str,
        method: &'static str,
    },
    /// The record `pcbridge-native idle-watch` keeps (KDE Plasma).
    WatchRecord,
}

pub const GNOME_ENDPOINTS: StateEndpoints = StateEndpoints {
    lock_destination: "org.gnome.ScreenSaver",
    lock_path: "/org/gnome/ScreenSaver",
    lock_interface: "org.gnome.ScreenSaver",
    idle: IdleSource::Dbus {
        destination: "org.gnome.Mutter.IdleMonitor",
        path: "/org/gnome/Mutter/IdleMonitor/Core",
        interface: "org.gnome.Mutter.IdleMonitor",
        method: "GetIdletime",
    },
};

pub const KDE_ENDPOINTS: StateEndpoints = StateEndpoints {
    lock_destination: "org.freedesktop.ScreenSaver",
    lock_path: "/ScreenSaver",
    lock_interface: "org.freedesktop.ScreenSaver",
    idle: IdleSource::WatchRecord,
};

impl StateEndpoints {
    #[must_use]
    pub const fn for_desktop(kind: DesktopKind) -> Self {
        match kind {
            DesktopKind::Gnome => GNOME_ENDPOINTS,
            DesktopKind::Kde => KDE_ENDPOINTS,
        }
    }
}
const DBUS_METHOD_TIMEOUT: Duration = Duration::from_millis(200);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u8)]
pub enum ScreenLockState {
    KnownLocked = 0,
    KnownUnlocked = 1,
    Unknown = 2,
}

impl ScreenLockState {
    #[must_use]
    pub const fn from_u8(value: u8) -> Self {
        match value {
            0 => Self::KnownLocked,
            1 => Self::KnownUnlocked,
            _ => Self::Unknown,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u8)]
pub enum ActivityState {
    Known = 0,
    Unknown = 1,
}

impl ActivityState {
    #[must_use]
    pub const fn from_u8(value: u8) -> Self {
        match value {
            0 => Self::Known,
            _ => Self::Unknown,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ScreenLockObservation {
    pub state: ScreenLockState,
    pub observed_at: SystemTime,
}

impl ScreenLockObservation {
    #[must_use]
    pub fn new(state: ScreenLockState) -> Self {
        Self {
            state,
            observed_at: SystemTime::now(),
        }
    }

    #[must_use]
    pub fn unknown() -> Self {
        Self::new(ScreenLockState::Unknown)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ActivityObservation {
    pub state: ActivityState,
    pub idle_ms: Option<u64>,
    pub observed_at: SystemTime,
}

impl ActivityObservation {
    #[must_use]
    pub fn new(state: ActivityState, idle_ms: Option<u64>) -> Self {
        let (state, idle_ms) = match (state, idle_ms) {
            (ActivityState::Known, Some(value)) => (ActivityState::Known, Some(value)),
            _ => (ActivityState::Unknown, None),
        };
        Self {
            state,
            idle_ms,
            observed_at: SystemTime::now(),
        }
    }

    #[must_use]
    pub fn unknown() -> Self {
        Self::new(ActivityState::Unknown, None)
    }
}

pub trait DesktopStateProvider: Send + Sync {
    fn screen_lock(&self) -> ScreenLockObservation;

    fn user_activity(&self) -> ActivityObservation;

    fn wait_for_lock_change(&self, timeout: Duration) -> ScreenLockObservation {
        thread::sleep(timeout);
        self.screen_lock()
    }
}

pub struct SessionDesktopState {
    connection: Connection,
    endpoints: StateEndpoints,
    lock_signals: Mutex<SignalStream<'static>>,
}

impl SessionDesktopState {
    /// The provider for this session's desktop.
    pub fn connect() -> Result<Self, zbus::Error> {
        Self::connect_to(StateEndpoints::for_desktop(DesktopKind::detect()))
    }

    pub fn connect_to(endpoints: StateEndpoints) -> Result<Self, zbus::Error> {
        let connection = zbus::block_on(
            Builder::session()?
                .method_timeout(DBUS_METHOD_TIMEOUT)
                .build(),
        )?;
        let proxy = zbus::block_on(Proxy::new(
            &connection,
            endpoints.lock_destination,
            endpoints.lock_path,
            endpoints.lock_interface,
        ))?;
        let lock_signals = zbus::block_on(proxy.receive_signal("ActiveChanged"))?;
        Ok(Self {
            connection,
            endpoints,
            lock_signals: Mutex::new(lock_signals),
        })
    }

    fn read_screen_lock(&self) -> ScreenLockObservation {
        let result: Result<bool, zbus::Error> = zbus::block_on(async {
            let proxy = Proxy::new(
                &self.connection,
                self.endpoints.lock_destination,
                self.endpoints.lock_path,
                self.endpoints.lock_interface,
            )
            .await?;
            proxy.call("GetActive", &()).await
        });
        result.map_or_else(
            |_| ScreenLockObservation::unknown(),
            |locked| {
                ScreenLockObservation::new(if locked {
                    ScreenLockState::KnownLocked
                } else {
                    ScreenLockState::KnownUnlocked
                })
            },
        )
    }

    fn read_user_activity(&self) -> ActivityObservation {
        let idle_ms = match self.endpoints.idle {
            IdleSource::Dbus {
                destination,
                path,
                interface,
                method,
            } => {
                let result: Result<u64, zbus::Error> = zbus::block_on(async {
                    let proxy = Proxy::new(&self.connection, destination, path, interface).await?;
                    proxy.call(method, &()).await
                });
                result.ok()
            }
            IdleSource::WatchRecord => idle::state_path()
                .and_then(|path| idle::read_idle_ms(&path, idle::unix_ms(SystemTime::now()))),
        };
        idle_ms.map_or_else(ActivityObservation::unknown, |idle_ms| {
            ActivityObservation::new(ActivityState::Known, Some(idle_ms))
        })
    }

    fn lock_from_signal(message: Message) -> ScreenLockObservation {
        message.body().deserialize::<bool>().map_or_else(
            |_| ScreenLockObservation::unknown(),
            |locked| {
                ScreenLockObservation::new(if locked {
                    ScreenLockState::KnownLocked
                } else {
                    ScreenLockState::KnownUnlocked
                })
            },
        )
    }
}

impl DesktopStateProvider for SessionDesktopState {
    fn screen_lock(&self) -> ScreenLockObservation {
        self.read_screen_lock()
    }

    fn user_activity(&self) -> ActivityObservation {
        self.read_user_activity()
    }

    fn wait_for_lock_change(&self, timeout: Duration) -> ScreenLockObservation {
        enum Wake {
            Signal(Option<Message>),
            Timeout,
        }

        let Ok(mut signals) = self.lock_signals.lock() else {
            return ScreenLockObservation::unknown();
        };
        let wake = zbus::block_on(future::or(
            async { Wake::Signal(signals.next().await) },
            async {
                Timer::after(timeout).await;
                Wake::Timeout
            },
        ));
        match wake {
            Wake::Signal(Some(message)) => Self::lock_from_signal(message),
            Wake::Signal(None) => ScreenLockObservation::unknown(),
            Wake::Timeout => self.read_screen_lock(),
        }
    }
}

pub struct UnknownDesktopState;

impl DesktopStateProvider for UnknownDesktopState {
    fn screen_lock(&self) -> ScreenLockObservation {
        ScreenLockObservation::unknown()
    }

    fn user_activity(&self) -> ActivityObservation {
        ActivityObservation::unknown()
    }
}

#[cfg(feature = "test-harness")]
pub struct DeterministicDesktopState;

#[cfg(feature = "test-harness")]
impl DesktopStateProvider for DeterministicDesktopState {
    fn screen_lock(&self) -> ScreenLockObservation {
        ScreenLockObservation::new(ScreenLockState::KnownUnlocked)
    }

    fn user_activity(&self) -> ActivityObservation {
        ActivityObservation::new(ActivityState::Known, Some(u64::MAX))
    }
}
