//! Typed GNOME screen-lock and user-activity observations.

use std::sync::Mutex;
use std::thread;
use std::time::{Duration, SystemTime};

use async_io::Timer;
use futures_lite::{StreamExt, future};
use zbus::{Connection, Message, Proxy, connection::Builder, proxy::SignalStream};

const SCREEN_SAVER_DESTINATION: &str = "org.gnome.ScreenSaver";
const SCREEN_SAVER_PATH: &str = "/org/gnome/ScreenSaver";
const SCREEN_SAVER_INTERFACE: &str = "org.gnome.ScreenSaver";
const IDLE_MONITOR_DESTINATION: &str = "org.gnome.Mutter.IdleMonitor";
const IDLE_MONITOR_PATH: &str = "/org/gnome/Mutter/IdleMonitor/Core";
const IDLE_MONITOR_INTERFACE: &str = "org.gnome.Mutter.IdleMonitor";
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

pub struct GnomeDesktopState {
    connection: Connection,
    lock_signals: Mutex<SignalStream<'static>>,
}

impl GnomeDesktopState {
    pub fn connect() -> Result<Self, zbus::Error> {
        let connection = zbus::block_on(
            Builder::session()?
                .method_timeout(DBUS_METHOD_TIMEOUT)
                .build(),
        )?;
        let proxy = zbus::block_on(Proxy::new(
            &connection,
            SCREEN_SAVER_DESTINATION,
            SCREEN_SAVER_PATH,
            SCREEN_SAVER_INTERFACE,
        ))?;
        let lock_signals = zbus::block_on(proxy.receive_signal("ActiveChanged"))?;
        Ok(Self {
            connection,
            lock_signals: Mutex::new(lock_signals),
        })
    }

    fn read_screen_lock(&self) -> ScreenLockObservation {
        let result: Result<bool, zbus::Error> = zbus::block_on(async {
            let proxy = Proxy::new(
                &self.connection,
                SCREEN_SAVER_DESTINATION,
                SCREEN_SAVER_PATH,
                SCREEN_SAVER_INTERFACE,
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
        let result: Result<u64, zbus::Error> = zbus::block_on(async {
            let proxy = Proxy::new(
                &self.connection,
                IDLE_MONITOR_DESTINATION,
                IDLE_MONITOR_PATH,
                IDLE_MONITOR_INTERFACE,
            )
            .await?;
            proxy.call("GetIdletime", &()).await
        });
        result.map_or_else(
            |_| ActivityObservation::unknown(),
            |idle_ms| ActivityObservation::new(ActivityState::Known, Some(idle_ms)),
        )
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

impl DesktopStateProvider for GnomeDesktopState {
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
