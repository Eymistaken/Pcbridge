//! Mutter ScreenCast session lifecycle.
//!
//! A screenshot on this machine is a *screen share*, not a photograph: the
//! photo interfaces flash and beep, `org.gnome.Shell.Screenshot` answers
//! "Access denied" on GNOME 46, and only the sharing path is silent. Sharing
//! means a session: `CreateSession`, one `RecordMonitor` per connector, then
//! `Start`, and the PipeWire node id for each connector arrives afterwards as a
//! `PipeWireStreamAdded` signal. `screencast_helper.py` does exactly this today
//! through PyGObject; this module does it through zbus so the helper process --
//! and the system Python it needs -- can eventually go away.
//!
//! Three things make the session worth its own state machine rather than a pair
//! of booleans:
//!
//! * **The session is visible to the user.** While it is open GNOME shows the
//!   sharing indicator in the top bar, and that indicator is the user's only
//!   evidence that an agent can see the screen. A session that outlives the
//!   grant makes the indicator lie. So every closure trigger -- revoke, screen
//!   lock, stream timeout, host EOF, compositor loss -- has to end in `Closed`,
//!   never in "probably closed".
//! * **Startup is not atomic.** By the time `RecordMonitor` fails for the
//!   second monitor, the compositor already holds a session and one stream. The
//!   only correct answer is to stop what was created, which means the failure
//!   path needs to know what exists.
//! * **It has to be testable without a compositor.** The bus is a trait, so
//!   `tests/capture_session.rs` drives early signals, missing streams, double
//!   start/stop and revoke-during-start against a fake, deterministically and
//!   with no screen sharing actually starting.
//!
//! Nothing in the protocol opens a session yet -- `dispatch.rs` has no method
//! that reaches this module. Task 3.3 sends the capture request and task 3.4
//! binds it to the Python shot pipeline; until then this is a library with its
//! own tests, and the Python helper stays the only path that shares a screen.

use std::collections::{BTreeMap, VecDeque};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use async_io::Timer;
use futures_lite::{StreamExt, future};
use zbus::message::Type as MessageType;
use zbus::zvariant::Value;
use zbus::{Connection, MatchRule, MessageStream, Proxy, connection::Builder};

use crate::lifecycle::LifecycleFailure;

const DESTINATION: &str = "org.gnome.Mutter.ScreenCast";
const OBJECT_PATH: &str = "/org/gnome/Mutter/ScreenCast";
const INTERFACE: &str = "org.gnome.Mutter.ScreenCast";
const SESSION_INTERFACE: &str = "org.gnome.Mutter.ScreenCast.Session";
const STREAM_INTERFACE: &str = "org.gnome.Mutter.ScreenCast.Stream";
const STREAM_ADDED: &str = "PipeWireStreamAdded";

/// D-Bus call ceiling for the session methods.
///
/// Deliberately *not* the 200 ms used for `GetActive`, `GetIdletime` and
/// `GetCurrentState`: those read a value the compositor already has, while
/// `Start` negotiates a PipeWire stream. The Python helper -- the only measured
/// implementation of this sequence -- allows 10 s per call and measured ~250 ms
/// for a whole cold setup on this machine, so a 200 ms ceiling would turn a
/// normal start into a failure. It is still finite: a hung compositor must not
/// hold a capture request open forever.
const CALL_TIMEOUT: Duration = Duration::from_secs(10);

/// How long `Start` may take to produce every PipeWire node.
///
/// Same value the Python helper uses. A connector that never reports a node
/// (monitor asleep, compositor wedged) fails the start instead of leaving a
/// half-mapped session behind.
pub const STREAM_TIMEOUT: Duration = Duration::from_secs(10);

/// Transitions kept for diagnosis. Bounded because a long-lived process can
/// recreate the session on every cursor change.
const HISTORY_LIMIT: usize = 16;

/// `RecordMonitor`'s `cursor-mode`.
///
/// Mutter also defines `2` (cursor delivered as separate metadata). We do not
/// ask for it: reading pointer metadata out of the compositor is the beginning
/// of the RemoteDesktop pointer permission, which this migration explicitly
/// does not request.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u32)]
pub enum CursorMode {
    Hidden = 0,
    Embedded = 1,
}

impl CursorMode {
    #[must_use]
    pub const fn as_u32(self) -> u32 {
        self as u32
    }

    #[must_use]
    pub const fn embedded(embedded: bool) -> Self {
        if embedded {
            Self::Embedded
        } else {
            Self::Hidden
        }
    }
}

/// `Failed` is a state of its own, not a flavor of `Closed`: both hold no
/// compositor resources, but `Failed` remembers why the last attempt died.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SessionState {
    Closed,
    Starting,
    Ready,
    Stopping,
    Failed,
}

/// One `PipeWireStreamAdded`: which stream object, which PipeWire node.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StreamAdded {
    pub stream: String,
    pub node: u32,
}

#[derive(Debug, thiserror::Error)]
pub enum BusError {
    #[error("{method} failed: {message}")]
    Call {
        method: &'static str,
        message: String,
    },
    /// The connection itself is gone. Callers must not try to tidy up over it:
    /// a `Stop` sent down a dead socket only produces a second error.
    #[error("compositor connection lost: {0}")]
    Lost(String),
}

/// The compositor side of a screencast session.
///
/// Implemented once for real Mutter and once for a fake in the tests. Keeping
/// the sequencing above this line is what makes "revoke arrives between
/// `RecordMonitor` and `Start`" a test rather than a thought experiment.
pub trait ScreenCastBus {
    fn create_session(&self) -> Result<String, BusError>;

    fn record_monitor(
        &self,
        session: &str,
        connector: &str,
        cursor: CursorMode,
    ) -> Result<String, BusError>;

    fn start(&self, session: &str) -> Result<(), BusError>;

    fn stop(&self, session: &str) -> Result<(), BusError>;

    /// The next stream announcement, or `None` once `deadline` has passed.
    ///
    /// Implementations must **buffer**: a signal that arrives before the caller
    /// starts waiting -- between `RecordMonitor` and `Start`, say -- has to be
    /// returned here rather than dropped. `None` means the deadline is over and
    /// the caller stops waiting, so returning it early turns a working start
    /// into a spurious timeout.
    fn next_stream(&self, deadline: Instant) -> Result<Option<StreamAdded>, BusError>;
}

/// Whether the desktop grant still permits holding a capture session.
///
/// `Lifecycle` implements it; the tests supply a fake that can revoke at an
/// exact step of the startup sequence.
pub trait SessionGuard {
    fn check(&self) -> Result<(), LifecycleFailure>;
}

impl SessionGuard for crate::lifecycle::Lifecycle {
    fn check(&self) -> Result<(), LifecycleFailure> {
        self.validate_now()
    }
}

/// Raised by the watchdog thread, read at every checkpoint of a running start.
///
/// The watchdog cannot simply take the session lock: a start it interrupts may
/// hold that lock for as long as the stream timeout, and a fail-closed signal
/// that waits ten seconds is not fail-closed. So it raises this flag first and
/// only then tries the lock; whichever call is in flight aborts itself at its
/// next checkpoint.
///
/// A poisoned lock reads as "raised". A panic somewhere in the session is not a
/// reason to keep sharing the screen.
#[derive(Debug, Default)]
pub struct FailClosedFlag(Mutex<Option<LifecycleFailure>>);

impl FailClosedFlag {
    #[must_use]
    pub fn new() -> Self {
        Self(Mutex::new(None))
    }

    pub fn raise(&self, reason: LifecycleFailure) {
        if let Ok(mut slot) = self.0.lock() {
            *slot = Some(reason);
        }
    }

    #[must_use]
    pub fn reason(&self) -> Option<LifecycleFailure> {
        self.0
            .lock()
            .map_or(Some(LifecycleFailure::Revoked), |slot| *slot)
    }

    pub fn clear(&self) {
        if let Ok(mut slot) = self.0.lock() {
            *slot = None;
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq, thiserror::Error)]
pub enum SessionFailure {
    #[error("desktop grant is not usable: {}", .0.code())]
    Guard(LifecycleFailure),
    #[error("{method} failed: {message}")]
    Call {
        method: &'static str,
        message: String,
    },
    #[error("compositor connection lost: {0}")]
    Lost(String),
    #[error("no PipeWire node arrived for: {}", .0.join(", "))]
    MissingStream(Vec<String>),
    #[error("a capture session needs at least one monitor")]
    NoMonitors,
    #[error("session is mid-transition: {0:?}")]
    Busy(SessionState),
}

impl SessionFailure {
    #[must_use]
    pub const fn is_transport_loss(&self) -> bool {
        matches!(self, Self::Lost(_))
    }
}

impl From<BusError> for SessionFailure {
    fn from(error: BusError) -> Self {
        match error {
            BusError::Call { method, message } => Self::Call { method, message },
            BusError::Lost(message) => Self::Lost(message),
        }
    }
}

/// What a session should be recording.
///
/// `topology_id` comes from task 3.1's display snapshot. It is carried here
/// because the connector to node mapping is only meaningful under the layout it
/// was built in: if the monitor table moves, a node id that still resolves may
/// now describe a different screen, and a capture would land silently on the
/// wrong one.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct StartRequest {
    pub monitors: Vec<String>,
    pub cursor: CursorMode,
    pub topology_id: String,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum OpenOutcome {
    /// Nothing was open; a session was created.
    Opened,
    /// An open session already matched the request; the bus was not touched.
    Reused,
    /// An open session did not match; it was stopped and a new one created.
    Recreated,
    /// The backend keeps no session (KWin's ScreenShot2): nothing to open.
    NotNeeded,
}

/// The session state machine. One entry point, `open`, so that cursor changes,
/// layout changes and plain first starts cannot drift apart.
pub struct CaptureSession<B: ScreenCastBus> {
    bus: B,
    state: SessionState,
    failure: Option<SessionFailure>,
    session_path: Option<String>,
    /// connector -> PipeWire node id, valid only under `topology_id`.
    nodes: BTreeMap<String, u32>,
    /// connector -> Stream object path, kept so a signal can be attributed.
    streams: BTreeMap<String, String>,
    monitors: Vec<String>,
    cursor: CursorMode,
    topology_id: String,
    history: VecDeque<SessionState>,
    stream_timeout: Duration,
    fail_closed: Arc<FailClosedFlag>,
}

impl<B: ScreenCastBus> std::fmt::Debug for CaptureSession<B> {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("CaptureSession")
            .field("state", &self.state)
            .field("monitors", &self.monitors)
            .field("cursor", &self.cursor)
            .field("failure", &self.failure)
            .finish_non_exhaustive()
    }
}

impl<B: ScreenCastBus> CaptureSession<B> {
    #[must_use]
    pub fn new(bus: B) -> Self {
        Self {
            bus,
            state: SessionState::Closed,
            failure: None,
            session_path: None,
            nodes: BTreeMap::new(),
            streams: BTreeMap::new(),
            monitors: Vec::new(),
            cursor: CursorMode::Embedded,
            topology_id: String::new(),
            history: VecDeque::from([SessionState::Closed]),
            stream_timeout: STREAM_TIMEOUT,
            fail_closed: Arc::new(FailClosedFlag::new()),
        }
    }

    #[must_use]
    pub fn with_stream_timeout(mut self, timeout: Duration) -> Self {
        self.stream_timeout = timeout;
        self
    }

    /// The flag the watchdog raises. Handing it out is the only way a thread
    /// that cannot take the session lock can still stop a start in flight.
    #[must_use]
    pub fn fail_closed_flag(&self) -> Arc<FailClosedFlag> {
        Arc::clone(&self.fail_closed)
    }

    #[must_use]
    pub const fn state(&self) -> SessionState {
        self.state
    }

    #[must_use]
    pub const fn is_open(&self) -> bool {
        matches!(self.state, SessionState::Ready)
    }

    #[must_use]
    pub fn failure(&self) -> Option<&SessionFailure> {
        self.failure.as_ref()
    }

    #[must_use]
    pub fn monitors(&self) -> Vec<String> {
        self.monitors.clone()
    }

    #[must_use]
    pub const fn cursor(&self) -> CursorMode {
        self.cursor
    }

    #[must_use]
    pub fn topology_id(&self) -> &str {
        &self.topology_id
    }

    /// The PipeWire node for a connector, or `None` when this session does not
    /// record it. Never falls back to "the first node": a capture that quietly
    /// lands on another monitor is the failure this whole layer exists to
    /// prevent.
    #[must_use]
    pub fn node_for(&self, connector: &str) -> Option<u32> {
        if self.is_open() {
            self.nodes.get(connector).copied()
        } else {
            None
        }
    }

    /// Edge log of the state machine, oldest first.
    #[must_use]
    pub fn history(&self) -> Vec<SessionState> {
        self.history.iter().copied().collect()
    }

    /// Bring the session in line with `request`.
    ///
    /// Subsumes what the Python helper splits into `start` and `ensure_cursor`.
    /// It also closes a gap there: `ScreenCast.start` returns `already: True`
    /// for *any* monitor list once a session is open, so asking for a different
    /// set silently keeps the old one. Here a different set recreates.
    pub fn open(
        &mut self,
        request: &StartRequest,
        guard: &dyn SessionGuard,
    ) -> Result<OpenOutcome, SessionFailure> {
        // Refused before anything moves: a malformed request must not knock a
        // healthy session out of `Ready`.
        if request.monitors.is_empty() {
            return Err(SessionFailure::NoMonitors);
        }
        // The flag interrupts work in flight; it is not a second authority on
        // whether capture is allowed. Clearing it here and asking the guard
        // below keeps one answer to that question -- and keeps a screen lock,
        // which ends when the user comes back, from latching the session shut.
        self.fail_closed.clear();

        let recreating = match self.state {
            SessionState::Ready if self.matches(request) => return Ok(OpenOutcome::Reused),
            // A stop that fails still leaves us closed, and the fresh
            // `CreateSession` below reports the real state of the bus.
            SessionState::Ready => {
                let _ = self.stop();
                true
            }
            SessionState::Closed | SessionState::Failed => false,
            // Unreachable while every entry point takes `&mut self`; kept
            // because reaching it would mean two callers are writing the same
            // connector map.
            state @ (SessionState::Starting | SessionState::Stopping) => {
                return Err(SessionFailure::Busy(state));
            }
        };

        self.transition(SessionState::Starting);
        match self.start_sequence(request, guard) {
            Ok(()) => {
                self.monitors.clone_from(&request.monitors);
                self.cursor = request.cursor;
                self.topology_id.clone_from(&request.topology_id);
                self.failure = None;
                self.transition(SessionState::Ready);
                Ok(if recreating {
                    OpenOutcome::Recreated
                } else {
                    OpenOutcome::Opened
                })
            }
            Err(failure) => {
                self.abort(failure.clone());
                Err(failure)
            }
        }
    }

    /// Close the session. Idempotent.
    ///
    /// `Ok(true)` closed an open session, `Ok(false)` there was nothing to
    /// close. An `Err` means the compositor refused or vanished -- and the
    /// session is **still** `Closed`, because the alternative is believing we
    /// hold a share we cannot stop.
    pub fn stop(&mut self) -> Result<bool, SessionFailure> {
        if matches!(self.state, SessionState::Closed) {
            return Ok(false);
        }
        let session = self.session_path.take();
        // Only a session that exists is worth a `Stopping` state; a `Failed`
        // start already released everything it had.
        let result = match session.as_deref() {
            Some(path) => {
                self.transition(SessionState::Stopping);
                self.bus.stop(path)
            }
            None => Ok(()),
        };
        self.clear();
        self.failure = None;
        self.transition(SessionState::Closed);
        match result {
            Ok(()) => Ok(session.is_some()),
            Err(error) => Err(error.into()),
        }
    }

    fn matches(&self, request: &StartRequest) -> bool {
        if self.cursor != request.cursor || self.topology_id != request.topology_id {
            return false;
        }
        let mut wanted = request.monitors.clone();
        wanted.sort();
        wanted.dedup();
        let recorded: Vec<String> = self.nodes.keys().cloned().collect();
        wanted == recorded
    }

    fn start_sequence(
        &mut self,
        request: &StartRequest,
        guard: &dyn SessionGuard,
    ) -> Result<(), SessionFailure> {
        self.checkpoint(guard)?;
        let session = self.bus.create_session()?;
        self.session_path = Some(session.clone());

        // stream object path -> connector, emptied as the nodes arrive.
        let mut pending: BTreeMap<String, String> = BTreeMap::new();
        for connector in &request.monitors {
            self.checkpoint(guard)?;
            let stream = self
                .bus
                .record_monitor(&session, connector, request.cursor)?;
            pending.insert(stream.clone(), connector.clone());
            self.streams.insert(connector.clone(), stream);
        }

        self.checkpoint(guard)?;
        self.bus.start(&session)?;

        let deadline = Instant::now() + self.stream_timeout;
        while !pending.is_empty() {
            let Some(added) = self.bus.next_stream(deadline)? else {
                break;
            };
            match pending.remove(&added.stream) {
                Some(connector) => {
                    self.nodes.insert(connector, added.node);
                }
                // Another client's screencast. The match rule is per interface,
                // not per object, so foreign announcements do reach us; they
                // are skipped rather than treated as ours or as an error.
                None => continue,
            }
        }

        if !pending.is_empty() {
            let mut missing: Vec<String> = pending.into_values().collect();
            missing.sort();
            return Err(SessionFailure::MissingStream(missing));
        }

        // The last checkpoint matters as much as the first: a revoke that lands
        // while we were waiting for nodes must not produce a `Ready` session.
        self.checkpoint(guard)
    }

    fn checkpoint(&self, guard: &dyn SessionGuard) -> Result<(), SessionFailure> {
        if let Some(reason) = self.fail_closed.reason() {
            return Err(SessionFailure::Guard(reason));
        }
        guard.check().map_err(SessionFailure::Guard)
    }

    /// Release whatever the failed start created, then remember why.
    fn abort(&mut self, failure: SessionFailure) {
        if let Some(session) = self.session_path.take() {
            self.transition(SessionState::Stopping);
            if !failure.is_transport_loss() {
                let _ = self.bus.stop(&session);
            }
        }
        self.clear();
        self.failure = Some(failure);
        self.transition(SessionState::Failed);
    }

    fn clear(&mut self) {
        self.session_path = None;
        self.nodes.clear();
        self.streams.clear();
        self.monitors.clear();
        self.topology_id.clear();
    }

    fn transition(&mut self, next: SessionState) {
        if self.state == next {
            return;
        }
        self.state = next;
        if self.history.len() == HISTORY_LIMIT {
            self.history.pop_front();
        }
        self.history.push_back(next);
    }
}

impl<B: ScreenCastBus> Drop for CaptureSession<B> {
    /// Host EOF, a panic, or plain shutdown: the process is going away and the
    /// compositor must not be left believing the share is still wanted.
    fn drop(&mut self) {
        if let Some(session) = self.session_path.take() {
            let _ = self.bus.stop(&session);
        }
    }
}

/// A session plus the flag the watchdog raises: the shape a `Lifecycle` can own.
///
/// The lock is never held across a `close_fail_closed` wait. If a start is in
/// flight the flag alone ends it, and the closing thread returns immediately.
pub struct SessionHandle<B: ScreenCastBus> {
    session: Mutex<CaptureSession<B>>,
    fail_closed: Arc<FailClosedFlag>,
}

impl<B: ScreenCastBus> std::fmt::Debug for SessionHandle<B> {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("SessionHandle")
            .field("fail_closed", &self.fail_closed.reason())
            .finish_non_exhaustive()
    }
}

impl<B: ScreenCastBus> SessionHandle<B> {
    #[must_use]
    pub fn new(session: CaptureSession<B>) -> Self {
        let fail_closed = session.fail_closed_flag();
        Self {
            session: Mutex::new(session),
            fail_closed,
        }
    }

    /// Open or reuse the session, under the same rules as `CaptureSession::open`.
    pub fn open(
        &self,
        request: &StartRequest,
        guard: &dyn SessionGuard,
    ) -> Result<OpenOutcome, SessionFailure> {
        let mut session = self.locked();
        session.open(request, guard)
    }

    pub fn stop(&self) -> Result<bool, SessionFailure> {
        self.locked().stop()
    }

    #[must_use]
    pub fn state(&self) -> SessionState {
        self.locked().state()
    }

    #[must_use]
    pub fn node_for(&self, connector: &str) -> Option<u32> {
        self.locked().node_for(connector)
    }

    #[must_use]
    pub fn fail_closed_reason(&self) -> Option<LifecycleFailure> {
        self.fail_closed.reason()
    }

    fn locked(&self) -> std::sync::MutexGuard<'_, CaptureSession<B>> {
        self.session
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }
}

impl<B: ScreenCastBus + Send> crate::lifecycle::FailClosed for SessionHandle<B> {
    fn close_fail_closed(&self, reason: LifecycleFailure) {
        self.fail_closed.raise(reason);
        // `try_lock`, never `lock`, for two separate reasons. The watchdog one:
        // a start it interrupts can hold the lock for as long as the stream
        // timeout, and blocking here would hand that delay to every later
        // revoke. The re-entrant one: the guard consulted *inside* a start is
        // `Lifecycle`, which closes registered resources the moment it notices
        // the revoke -- so this can be reached with the lock already held by
        // this very thread, and `lock()` would deadlock against itself.
        // Either way the in-flight call reads the flag at its next checkpoint
        // and closes itself.
        if let Ok(mut session) = self.session.try_lock() {
            let _ = session.stop();
        }
    }
}

/// The real Mutter transport.
///
/// The signal subscription is created in `connect`, before any session exists,
/// so no announcement can land in the gap between `RecordMonitor` and the first
/// `next_stream`. It matches the `Stream` interface rather than one object path,
/// which is what makes that possible -- and why foreign streams have to be
/// filtered out above.
pub struct MutterScreenCast {
    connection: Connection,
    signals: Mutex<MessageStream>,
}

impl std::fmt::Debug for MutterScreenCast {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.debug_struct("MutterScreenCast").finish()
    }
}

impl MutterScreenCast {
    pub fn connect() -> Result<Self, zbus::Error> {
        let connection = zbus::block_on(Builder::session()?.method_timeout(CALL_TIMEOUT).build())?;
        let rule = MatchRule::builder()
            .msg_type(MessageType::Signal)
            .sender(DESTINATION)?
            .interface(STREAM_INTERFACE)?
            .member(STREAM_ADDED)?
            .build();
        let signals = zbus::block_on(MessageStream::for_match_rule(rule, &connection, Some(64)))?;
        Ok(Self {
            connection,
            signals: Mutex::new(signals),
        })
    }

    fn session_call(&self, session: &str, method: &'static str) -> Result<(), BusError> {
        let path = session.to_owned();
        zbus::block_on(async {
            let proxy = Proxy::new(&self.connection, DESTINATION, path, SESSION_INTERFACE)
                .await
                .map_err(|error| map_error(method, error))?;
            proxy
                .call::<_, _, ()>(method, &())
                .await
                .map_err(|error| map_error(method, error))
        })
    }
}

fn map_error(method: &'static str, error: zbus::Error) -> BusError {
    match error {
        zbus::Error::InputOutput(_) | zbus::Error::Failure(_) => BusError::Lost(error.to_string()),
        other => BusError::Call {
            method,
            message: other.to_string(),
        },
    }
}

impl ScreenCastBus for MutterScreenCast {
    fn create_session(&self) -> Result<String, BusError> {
        let properties: BTreeMap<&str, Value<'_>> = BTreeMap::new();
        zbus::block_on(async {
            let proxy = Proxy::new(&self.connection, DESTINATION, OBJECT_PATH, INTERFACE)
                .await
                .map_err(|error| map_error("CreateSession", error))?;
            proxy
                .call::<_, _, zbus::zvariant::OwnedObjectPath>("CreateSession", &(properties,))
                .await
                .map(|path| path.as_str().to_owned())
                .map_err(|error| map_error("CreateSession", error))
        })
    }

    fn record_monitor(
        &self,
        session: &str,
        connector: &str,
        cursor: CursorMode,
    ) -> Result<String, BusError> {
        let mut properties: BTreeMap<&str, Value<'_>> = BTreeMap::new();
        properties.insert("cursor-mode", Value::U32(cursor.as_u32()));
        let path = session.to_owned();
        zbus::block_on(async {
            let proxy = Proxy::new(&self.connection, DESTINATION, path, SESSION_INTERFACE)
                .await
                .map_err(|error| map_error("RecordMonitor", error))?;
            proxy
                .call::<_, _, zbus::zvariant::OwnedObjectPath>(
                    "RecordMonitor",
                    &(connector, properties),
                )
                .await
                .map(|path| path.as_str().to_owned())
                .map_err(|error| map_error("RecordMonitor", error))
        })
    }

    fn start(&self, session: &str) -> Result<(), BusError> {
        self.session_call(session, "Start")
    }

    fn stop(&self, session: &str) -> Result<(), BusError> {
        self.session_call(session, "Stop")
    }

    fn next_stream(&self, deadline: Instant) -> Result<Option<StreamAdded>, BusError> {
        enum Wake {
            Message(Option<Result<zbus::Message, zbus::Error>>),
            Deadline,
        }

        let mut signals = self
            .signals
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        loop {
            let wake = zbus::block_on(future::or(
                async { Wake::Message(signals.next().await) },
                async {
                    Timer::at(deadline).await;
                    Wake::Deadline
                },
            ));
            match wake {
                Wake::Deadline => return Ok(None),
                Wake::Message(None) => {
                    return Err(BusError::Lost("screencast signal stream ended".to_owned()));
                }
                Wake::Message(Some(Err(error))) => return Err(BusError::Lost(error.to_string())),
                Wake::Message(Some(Ok(message))) => {
                    let header = message.header();
                    let Some(path) = header.path().map(ToString::to_string) else {
                        continue;
                    };
                    let Ok(node) = message.body().deserialize::<u32>() else {
                        continue;
                    };
                    return Ok(Some(StreamAdded { stream: path, node }));
                }
            }
        }
    }
}
