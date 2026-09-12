//! Capture session lifecycle against a fake compositor.
//!
//! Nothing here talks to D-Bus, starts a screen share or needs a display. The
//! bus is a trait precisely so that the sequences that are hard to reproduce on
//! a real machine -- a stream announced before we wait for it, a node that never
//! arrives, a grant revoked between `RecordMonitor` and `Start` -- are ordinary
//! test cases instead of things we hope we got right.
//!
//! The fake records every call it receives. Most assertions are about that log:
//! what matters about a failed start is not only the error, but that the session
//! the compositor already created was stopped.

use std::collections::{BTreeMap, VecDeque};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use pcbridge_native::lifecycle::{FailClosed, Lifecycle, LifecycleFailure};
use pcbridge_native::platform::linux::desktop_state::{
    ActivityObservation, DesktopStateProvider, ScreenLockObservation, ScreenLockState,
};
use pcbridge_native::platform::linux::session::{
    BusError, CaptureSession, CursorMode, FailClosedFlag, OpenOutcome, ScreenCastBus,
    SessionFailure, SessionGuard, SessionHandle, SessionState, StartRequest, StreamAdded,
};

// ----------------------------------------------------------------- fake bus

/// When the fake announces a connector's PipeWire node.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Emit {
    /// Immediately when `RecordMonitor` returns -- before `Start`, before anyone
    /// waits. Mutter does not do this, but a buffering bug only shows up here.
    OnRecord,
    /// The realistic case: after `Start`.
    OnStart,
    /// Never. A monitor that is asleep behaves like this.
    Never,
}

/// A scripted failure. `BusError` is not `Clone`, so the script stores the
/// intent and builds the error when it fires.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
enum Fault {
    #[default]
    None,
    Call,
    Lost,
}

impl Fault {
    fn fire(self, method: &'static str) -> Result<(), BusError> {
        match self {
            Self::None => Ok(()),
            Self::Call => Err(BusError::Call {
                method,
                message: "scripted refusal".to_owned(),
            }),
            Self::Lost => Err(BusError::Lost("scripted disconnect".to_owned())),
        }
    }
}

#[derive(Default)]
struct Script {
    sessions: u32,
    streams: u32,
    emit: BTreeMap<String, Emit>,
    on_start: Vec<StreamAdded>,
    queued: VecDeque<StreamAdded>,
    foreign: Vec<StreamAdded>,
    create_fault: Fault,
    record_faults: BTreeMap<String, Fault>,
    start_fault: Fault,
    stop_fault: Fault,
}

#[derive(Clone)]
struct FakeBus {
    ops: Arc<Mutex<Vec<String>>>,
    script: Arc<Mutex<Script>>,
}

impl FakeBus {
    fn new() -> Self {
        Self {
            ops: Arc::new(Mutex::new(Vec::new())),
            script: Arc::new(Mutex::new(Script::default())),
        }
    }

    fn ops(&self) -> Vec<String> {
        self.ops.lock().expect("ops lock").clone()
    }

    fn record_op(&self, op: impl Into<String>) {
        self.ops.lock().expect("ops lock").push(op.into());
    }

    fn emit(&self, connector: &str, when: Emit) {
        self.script
            .lock()
            .expect("script lock")
            .emit
            .insert(connector.to_owned(), when);
    }

    fn foreign(&self, stream: &str, node: u32) {
        self.script
            .lock()
            .expect("script lock")
            .foreign
            .push(StreamAdded {
                stream: stream.to_owned(),
                node,
            });
    }

    fn fault_create(&self, fault: Fault) {
        self.script.lock().expect("script lock").create_fault = fault;
    }

    fn fault_record(&self, connector: &str, fault: Fault) {
        self.script
            .lock()
            .expect("script lock")
            .record_faults
            .insert(connector.to_owned(), fault);
    }

    fn fault_start(&self, fault: Fault) {
        self.script.lock().expect("script lock").start_fault = fault;
    }

    fn fault_stop(&self, fault: Fault) {
        self.script.lock().expect("script lock").stop_fault = fault;
    }
}

impl ScreenCastBus for FakeBus {
    fn create_session(&self) -> Result<String, BusError> {
        self.record_op("create");
        let mut script = self.script.lock().expect("script lock");
        script.create_fault.fire("CreateSession")?;
        script.sessions += 1;
        Ok(format!("/session/{}", script.sessions))
    }

    fn record_monitor(
        &self,
        _session: &str,
        connector: &str,
        cursor: CursorMode,
    ) -> Result<String, BusError> {
        self.record_op(format!("record:{connector}:{}", cursor.as_u32()));
        let mut script = self.script.lock().expect("script lock");
        script
            .record_faults
            .get(connector)
            .copied()
            .unwrap_or_default()
            .fire("RecordMonitor")?;
        script.streams += 1;
        let stream = format!("/stream/{}", script.streams);
        let added = StreamAdded {
            stream: stream.clone(),
            node: 100 + script.streams,
        };
        match script.emit.get(connector).copied().unwrap_or(Emit::OnStart) {
            Emit::OnRecord => script.queued.push_back(added),
            Emit::OnStart => script.on_start.push(added),
            Emit::Never => {}
        }
        Ok(stream)
    }

    fn start(&self, _session: &str) -> Result<(), BusError> {
        self.record_op("start");
        let mut script = self.script.lock().expect("script lock");
        script.start_fault.fire("Start")?;
        // Foreign announcements land first: another client's session is not a
        // reason to stop waiting for ours.
        let foreign = std::mem::take(&mut script.foreign);
        for added in foreign {
            script.queued.push_back(added);
        }
        let pending = std::mem::take(&mut script.on_start);
        for added in pending {
            script.queued.push_back(added);
        }
        Ok(())
    }

    fn stop(&self, _session: &str) -> Result<(), BusError> {
        self.record_op("stop");
        let mut script = self.script.lock().expect("script lock");
        script.queued.clear();
        script.on_start.clear();
        script.stop_fault.fire("Stop")
    }

    fn next_stream(&self, _deadline: Instant) -> Result<Option<StreamAdded>, BusError> {
        // Empty means "the deadline passed": the fake never makes the caller
        // wait, so a missing node fails the start immediately instead of after
        // ten real seconds.
        Ok(self.script.lock().expect("script lock").queued.pop_front())
    }
}

// -------------------------------------------------------------- fake guards

/// Says yes until the given call, then reports a failure. Call zero is the
/// checkpoint before `CreateSession`.
struct StepGuard {
    calls: AtomicUsize,
    fail_at: usize,
    failure: LifecycleFailure,
}

impl StepGuard {
    fn always_open() -> Self {
        Self {
            calls: AtomicUsize::new(0),
            fail_at: usize::MAX,
            failure: LifecycleFailure::Revoked,
        }
    }

    fn revoking_at(step: usize) -> Self {
        Self {
            calls: AtomicUsize::new(0),
            fail_at: step,
            failure: LifecycleFailure::Revoked,
        }
    }
}

impl SessionGuard for StepGuard {
    fn check(&self) -> Result<(), LifecycleFailure> {
        if self.calls.fetch_add(1, Ordering::AcqRel) >= self.fail_at {
            Err(self.failure)
        } else {
            Ok(())
        }
    }
}

/// Always says yes, but raises the fail-closed flag at the given step -- what a
/// watchdog thread does when it cannot take the session lock.
struct WatchdogGuard {
    calls: AtomicUsize,
    raise_at: usize,
    flag: Arc<FailClosedFlag>,
}

impl SessionGuard for WatchdogGuard {
    fn check(&self) -> Result<(), LifecycleFailure> {
        if self.calls.fetch_add(1, Ordering::AcqRel) == self.raise_at {
            self.flag.raise(LifecycleFailure::ScreenLocked);
        }
        Ok(())
    }
}

// ------------------------------------------------------------------ helpers

fn request(monitors: &[&str]) -> StartRequest {
    StartRequest {
        monitors: monitors.iter().map(|name| (*name).to_owned()).collect(),
        cursor: CursorMode::Embedded,
        topology_id: "v1|0,0,1920,1080,1.0000,0,0|1920,0,1920,1080,1.0000,0,1".to_owned(),
    }
}

fn open_session(bus: &FakeBus) -> CaptureSession<FakeBus> {
    let mut session = CaptureSession::new(bus.clone());
    session
        .open(&request(&["DP-4", "DP-3"]), &StepGuard::always_open())
        .expect("the happy path should open");
    session
}

// -------------------------------------------------------------------- tests

#[test]
fn a_start_maps_every_connector_to_its_own_node() {
    let bus = FakeBus::new();
    let mut session = CaptureSession::new(bus.clone());

    let outcome = session
        .open(&request(&["DP-4", "DP-3"]), &StepGuard::always_open())
        .expect("start");

    assert_eq!(outcome, OpenOutcome::Opened);
    assert_eq!(session.state(), SessionState::Ready);
    assert_eq!(session.node_for("DP-4"), Some(101));
    assert_eq!(session.node_for("DP-3"), Some(102));
    assert_eq!(session.node_for("DP-9"), None);
    assert_eq!(
        bus.ops(),
        vec!["create", "record:DP-4:1", "record:DP-3:1", "start"]
    );
    assert_eq!(
        session.history(),
        vec![
            SessionState::Closed,
            SessionState::Starting,
            SessionState::Ready
        ]
    );
}

#[test]
fn a_stream_announced_before_start_is_not_lost() {
    let bus = FakeBus::new();
    bus.emit("DP-4", Emit::OnRecord);
    let mut session = CaptureSession::new(bus.clone());

    session
        .open(&request(&["DP-4", "DP-3"]), &StepGuard::always_open())
        .expect("an early announcement should still be applied");

    assert_eq!(session.node_for("DP-4"), Some(101));
    assert_eq!(session.node_for("DP-3"), Some(102));
}

#[test]
fn another_clients_stream_is_ignored() {
    let bus = FakeBus::new();
    bus.foreign("/somebody/else/Stream/7", 999);
    let mut session = CaptureSession::new(bus.clone());

    session
        .open(&request(&["DP-4"]), &StepGuard::always_open())
        .expect("a foreign announcement should not disturb our start");

    assert_eq!(session.node_for("DP-4"), Some(101));
    assert!(!session.history().contains(&SessionState::Failed));
}

#[test]
fn a_missing_node_fails_the_start_and_closes_the_session() {
    let bus = FakeBus::new();
    bus.emit("DP-3", Emit::Never);
    let mut session = CaptureSession::new(bus.clone());

    let failure = session
        .open(&request(&["DP-4", "DP-3"]), &StepGuard::always_open())
        .expect_err("a connector without a node must fail the start");

    assert_eq!(
        failure,
        SessionFailure::MissingStream(vec!["DP-3".to_owned()])
    );
    assert_eq!(session.state(), SessionState::Failed);
    assert_eq!(session.node_for("DP-4"), None);
    assert_eq!(bus.ops().last().map(String::as_str), Some("stop"));
}

#[test]
fn a_refused_record_monitor_stops_what_was_already_created() {
    let bus = FakeBus::new();
    bus.fault_record("DP-3", Fault::Call);
    let mut session = CaptureSession::new(bus.clone());

    let failure = session
        .open(&request(&["DP-4", "DP-3"]), &StepGuard::always_open())
        .expect_err("a refused RecordMonitor must fail the start");

    assert!(matches!(
        failure,
        SessionFailure::Call {
            method: "RecordMonitor",
            ..
        }
    ));
    assert_eq!(
        bus.ops(),
        vec!["create", "record:DP-4:1", "record:DP-3:1", "stop"]
    );
    assert_eq!(session.state(), SessionState::Failed);
}

#[test]
fn a_lost_connection_does_not_try_to_stop_over_it() {
    let bus = FakeBus::new();
    bus.fault_start(Fault::Lost);
    let mut session = CaptureSession::new(bus.clone());

    let failure = session
        .open(&request(&["DP-4"]), &StepGuard::always_open())
        .expect_err("a lost connection must fail the start");

    assert!(failure.is_transport_loss());
    assert_eq!(session.state(), SessionState::Failed);
    assert!(
        !bus.ops().contains(&"stop".to_owned()),
        "a Stop sent down a dead socket only produces a second error"
    );
}

#[test]
fn a_failed_create_session_leaves_nothing_behind() {
    let bus = FakeBus::new();
    bus.fault_create(Fault::Call);
    let mut session = CaptureSession::new(bus.clone());

    session
        .open(&request(&["DP-4"]), &StepGuard::always_open())
        .expect_err("a refused CreateSession must fail the start");

    assert_eq!(bus.ops(), vec!["create"]);
    assert_eq!(session.state(), SessionState::Failed);
}

#[test]
fn opening_twice_with_the_same_request_touches_nothing() {
    let bus = FakeBus::new();
    let mut session = open_session(&bus);
    let after_start = bus.ops();

    let outcome = session
        .open(&request(&["DP-4", "DP-3"]), &StepGuard::always_open())
        .expect("the second open should reuse");

    assert_eq!(outcome, OpenOutcome::Reused);
    assert_eq!(bus.ops(), after_start);
    assert_eq!(session.state(), SessionState::Ready);
}

#[test]
fn the_monitor_order_does_not_count_as_a_different_request() {
    let bus = FakeBus::new();
    let mut session = open_session(&bus);

    let outcome = session
        .open(&request(&["DP-3", "DP-4"]), &StepGuard::always_open())
        .expect("the same set in another order is the same session");

    assert_eq!(outcome, OpenOutcome::Reused);
}

#[test]
fn a_cursor_change_recreates_the_session() {
    let bus = FakeBus::new();
    let mut session = open_session(&bus);
    let mut hidden = request(&["DP-4", "DP-3"]);
    hidden.cursor = CursorMode::Hidden;

    let outcome = session
        .open(&hidden, &StepGuard::always_open())
        .expect("a cursor change should recreate");

    assert_eq!(outcome, OpenOutcome::Recreated);
    assert_eq!(session.cursor(), CursorMode::Hidden);
    assert_eq!(
        bus.ops(),
        vec![
            "create",
            "record:DP-4:1",
            "record:DP-3:1",
            "start",
            "stop",
            "create",
            "record:DP-4:0",
            "record:DP-3:0",
            "start",
        ]
    );
}

#[test]
fn a_layout_change_recreates_the_session() {
    let bus = FakeBus::new();
    let mut session = open_session(&bus);
    let mut moved = request(&["DP-4", "DP-3"]);
    moved.topology_id = "v1|0,0,1920,1080,1.0000,0,0".to_owned();

    let outcome = session
        .open(&moved, &StepGuard::always_open())
        .expect("a new topology should recreate");

    assert_eq!(outcome, OpenOutcome::Recreated);
    assert_eq!(session.topology_id(), "v1|0,0,1920,1080,1.0000,0,0");
}

#[test]
fn a_different_monitor_set_recreates_instead_of_silently_keeping_the_old_one() {
    let bus = FakeBus::new();
    let mut session = open_session(&bus);

    let outcome = session
        .open(&request(&["DP-4"]), &StepGuard::always_open())
        .expect("a different set should recreate");

    assert_eq!(outcome, OpenOutcome::Recreated);
    assert_eq!(session.monitors(), vec!["DP-4".to_owned()]);
    assert_eq!(session.node_for("DP-3"), None);
}

#[test]
fn stopping_twice_is_harmless() {
    let bus = FakeBus::new();
    let mut session = open_session(&bus);

    assert_eq!(session.stop(), Ok(true));
    assert_eq!(session.stop(), Ok(false));
    assert_eq!(session.state(), SessionState::Closed);
    assert_eq!(
        bus.ops().iter().filter(|op| *op == "stop").count(),
        1,
        "the compositor should be asked to stop exactly once"
    );
}

#[test]
fn a_refused_stop_still_leaves_the_session_closed() {
    let bus = FakeBus::new();
    bus.fault_stop(Fault::Call);
    let mut session = open_session(&bus);

    let result = session.stop();

    assert!(result.is_err(), "the caller should learn the stop failed");
    assert_eq!(
        session.state(),
        SessionState::Closed,
        "believing we still hold a share we cannot stop is worse than the error"
    );
    assert_eq!(session.node_for("DP-4"), None);
}

#[test]
fn stopping_a_failed_session_clears_it() {
    let bus = FakeBus::new();
    bus.emit("DP-4", Emit::Never);
    let mut session = CaptureSession::new(bus.clone());
    session
        .open(&request(&["DP-4"]), &StepGuard::always_open())
        .expect_err("start");
    assert_eq!(session.state(), SessionState::Failed);

    assert_eq!(session.stop(), Ok(false));
    assert_eq!(session.state(), SessionState::Closed);
    assert!(session.failure().is_none());
}

#[test]
fn a_revoke_between_record_monitor_calls_closes_the_created_session() {
    let bus = FakeBus::new();
    let mut session = CaptureSession::new(bus.clone());

    // Checkpoints: 0 before CreateSession, 1 before DP-4, 2 before DP-3.
    let failure = session
        .open(&request(&["DP-4", "DP-3"]), &StepGuard::revoking_at(2))
        .expect_err("a revoked grant must fail the start");

    assert_eq!(failure, SessionFailure::Guard(LifecycleFailure::Revoked));
    assert_eq!(bus.ops(), vec!["create", "record:DP-4:1", "stop"]);
    assert_eq!(session.state(), SessionState::Failed);
}

#[test]
fn a_revoke_before_create_session_never_touches_the_bus() {
    let bus = FakeBus::new();
    let mut session = CaptureSession::new(bus.clone());

    session
        .open(&request(&["DP-4"]), &StepGuard::revoking_at(0))
        .expect_err("a revoked grant must fail the start");

    assert!(bus.ops().is_empty());
    assert_eq!(session.state(), SessionState::Failed);
}

#[test]
fn a_revoke_after_the_nodes_arrive_still_refuses() {
    let bus = FakeBus::new();
    let mut session = CaptureSession::new(bus.clone());

    // Checkpoints for one monitor: 0 before CreateSession, 1 before DP-4,
    // 2 before Start, 3 after the nodes arrived.
    let failure = session
        .open(&request(&["DP-4"]), &StepGuard::revoking_at(3))
        .expect_err("a revoke during the stream wait must not produce Ready");

    assert_eq!(failure, SessionFailure::Guard(LifecycleFailure::Revoked));
    assert_eq!(session.state(), SessionState::Failed);
    assert_eq!(bus.ops().last().map(String::as_str), Some("stop"));
}

#[test]
fn the_fail_closed_flag_aborts_a_start_in_flight() {
    let bus = FakeBus::new();
    let mut session = CaptureSession::new(bus.clone());
    let flag = session.fail_closed_flag();
    let guard = WatchdogGuard {
        calls: AtomicUsize::new(0),
        raise_at: 1,
        flag: Arc::clone(&flag),
    };

    let failure = session
        .open(&request(&["DP-4", "DP-3"]), &guard)
        .expect_err("the watchdog flag must end the start");

    assert_eq!(
        failure,
        SessionFailure::Guard(LifecycleFailure::ScreenLocked),
        "the reason the watchdog gave is the reason the caller sees"
    );
    assert_eq!(session.state(), SessionState::Failed);
    assert_eq!(bus.ops().last().map(String::as_str), Some("stop"));
}

#[test]
fn a_raised_flag_does_not_latch_the_session_shut() {
    let bus = FakeBus::new();
    let mut session = CaptureSession::new(bus.clone());
    session
        .fail_closed_flag()
        .raise(LifecycleFailure::ScreenLocked);

    // The screen unlocks; the guard is the authority on whether capture is
    // allowed, and it now says yes.
    session
        .open(&request(&["DP-4"]), &StepGuard::always_open())
        .expect("an unlocked screen should be able to open again");

    assert_eq!(session.state(), SessionState::Ready);
}

/// Closes the handle from inside the guard -- what `Lifecycle` does when the
/// request path is the first to notice a revoke, on the very thread that is
/// holding the session lock.
struct ReentrantGuard {
    handle: Mutex<Option<Arc<SessionHandle<FakeBus>>>>,
    calls: AtomicUsize,
    close_at: usize,
}

impl SessionGuard for ReentrantGuard {
    fn check(&self) -> Result<(), LifecycleFailure> {
        if self.calls.fetch_add(1, Ordering::AcqRel) == self.close_at {
            let handle = self.handle.lock().expect("handle lock").clone();
            if let Some(handle) = handle {
                handle.close_fail_closed(LifecycleFailure::Revoked);
            }
        }
        Ok(())
    }
}

#[test]
fn closing_from_inside_the_guard_does_not_deadlock() {
    let bus = FakeBus::new();
    let handle = Arc::new(SessionHandle::new(CaptureSession::new(bus.clone())));
    let guard = ReentrantGuard {
        handle: Mutex::new(Some(Arc::clone(&handle))),
        calls: AtomicUsize::new(0),
        close_at: 1,
    };

    let failure = handle
        .open(&request(&["DP-4", "DP-3"]), &guard)
        .expect_err("a revoke noticed inside the guard must end the start");

    assert_eq!(failure, SessionFailure::Guard(LifecycleFailure::Revoked));
    // `Failed`, not `Closed`: the in-flight start owned the cleanup, and the
    // state remembers why it ended. Both hold nothing -- the created session
    // was stopped before the error came back.
    assert_eq!(handle.state(), SessionState::Failed);
    assert_eq!(handle.node_for("DP-4"), None);
    assert_eq!(bus.ops(), vec!["create", "record:DP-4:1", "stop"]);
}

#[test]
fn an_empty_monitor_list_is_refused_without_disturbing_an_open_session() {
    let bus = FakeBus::new();
    let mut session = open_session(&bus);
    let after_start = bus.ops();

    let failure = session
        .open(&request(&[]), &StepGuard::always_open())
        .expect_err("an empty list is not a session");

    assert_eq!(failure, SessionFailure::NoMonitors);
    assert_eq!(session.state(), SessionState::Ready);
    assert_eq!(bus.ops(), after_start);
}

#[test]
fn read_only_queries_never_touch_the_bus() {
    let bus = FakeBus::new();
    let session = CaptureSession::new(bus.clone());

    // Everything a capability answer could possibly want to know.
    assert_eq!(session.state(), SessionState::Closed);
    assert!(!session.is_open());
    assert!(session.failure().is_none());
    assert!(session.monitors().is_empty());
    assert_eq!(session.node_for("DP-4"), None);
    assert_eq!(session.topology_id(), "");

    assert!(
        bus.ops().is_empty(),
        "asking what the backend can do must not start sharing the screen"
    );
}

#[test]
fn dropping_the_session_stops_the_share() {
    let bus = FakeBus::new();
    let session = open_session(&bus);
    assert!(!bus.ops().contains(&"stop".to_owned()));

    drop(session);

    assert_eq!(
        bus.ops().last().map(String::as_str),
        Some("stop"),
        "host EOF must not leave the sharing indicator on"
    );
}

#[test]
fn a_handle_closes_the_session_when_the_watchdog_says_so() {
    let bus = FakeBus::new();
    let handle = SessionHandle::new(CaptureSession::new(bus.clone()));
    handle
        .open(&request(&["DP-4"]), &StepGuard::always_open())
        .expect("start");
    assert_eq!(handle.state(), SessionState::Ready);

    handle.close_fail_closed(LifecycleFailure::Revoked);

    assert_eq!(handle.state(), SessionState::Closed);
    assert_eq!(handle.node_for("DP-4"), None);
    assert_eq!(bus.ops().last().map(String::as_str), Some("stop"));
    assert_eq!(handle.fail_closed_reason(), Some(LifecycleFailure::Revoked));
}

// ------------------------------------------- registry wiring in `Lifecycle`

fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock should follow the Unix epoch")
        .as_secs_f64()
}

fn state_dir(name: &str) -> PathBuf {
    let root = std::env::temp_dir().join(format!(
        "pcbridge-capture-session-{name}-{}-{}",
        std::process::id(),
        now()
    ));
    fs::create_dir_all(&root).expect("state dir");
    root
}

fn write_grant(dir: &Path, seconds: f64) {
    let moment = now();
    let temporary = dir.join("desktop_unlock.json.tmp");
    fs::write(
        &temporary,
        serde_json::to_vec(&serde_json::json!({
            "schema_version": 1,
            "grant_id": "capture-session-test",
            "revoke_epoch": 1,
            "until": moment + seconds,
            "hard_until": moment + seconds,
            "reason": "capture session contract",
            "granted": moment,
            "granted_by": "desktop_unlock",
        }))
        .expect("grant json"),
    )
    .expect("write grant");
    fs::rename(temporary, dir.join("desktop_unlock.json")).expect("publish grant");
}

/// Records what the watchdog asked it to do.
#[derive(Default)]
struct ClosedResource {
    reasons: Mutex<Vec<LifecycleFailure>>,
}

impl ClosedResource {
    fn reasons(&self) -> Vec<LifecycleFailure> {
        self.reasons.lock().expect("reasons lock").clone()
    }
}

impl FailClosed for ClosedResource {
    fn close_fail_closed(&self, reason: LifecycleFailure) {
        self.reasons.lock().expect("reasons lock").push(reason);
    }
}

/// A screen that is unlocked until the test says otherwise.
struct FakeScreen {
    locked: AtomicBool,
}

impl DesktopStateProvider for FakeScreen {
    fn screen_lock(&self) -> ScreenLockObservation {
        ScreenLockObservation::new(if self.locked.load(Ordering::Acquire) {
            ScreenLockState::KnownLocked
        } else {
            ScreenLockState::KnownUnlocked
        })
    }

    fn user_activity(&self) -> ActivityObservation {
        ActivityObservation::unknown()
    }
}

fn wait_for(mut done: impl FnMut() -> bool) -> bool {
    let deadline = Instant::now() + Duration::from_secs(3);
    while Instant::now() < deadline {
        if done() {
            return true;
        }
        thread::sleep(Duration::from_millis(20));
    }
    done()
}

#[test]
fn revoking_the_grant_closes_registered_resources() {
    let dir = state_dir("revoke");
    write_grant(&dir, 30.0);
    let screen = Arc::new(FakeScreen {
        locked: AtomicBool::new(false),
    });
    let lifecycle = Lifecycle::start_with_provider(&dir, screen).expect("lifecycle");
    let resource = Arc::new(ClosedResource::default());
    lifecycle.register_fail_closed(Arc::clone(&resource) as Arc<dyn FailClosed>);
    assert!(resource.reasons().is_empty());

    write_grant(&dir, -1.0);

    assert!(
        wait_for(|| !resource.reasons().is_empty()),
        "the lease watchdog should have closed the resource"
    );
    assert_eq!(resource.reasons(), vec![LifecycleFailure::Revoked]);

    // Edge triggered: the watchdog keeps running, but a revoked grant does not
    // keep waking every registered resource ten times a second.
    thread::sleep(Duration::from_millis(400));
    assert_eq!(resource.reasons().len(), 1);
    let _ = fs::remove_dir_all(&dir);
}

#[test]
fn locking_the_screen_closes_registered_resources() {
    let dir = state_dir("lock");
    write_grant(&dir, 30.0);
    let screen = Arc::new(FakeScreen {
        locked: AtomicBool::new(false),
    });
    let lifecycle =
        Lifecycle::start_with_provider(&dir, Arc::clone(&screen) as Arc<dyn DesktopStateProvider>)
            .expect("lifecycle");
    let resource = Arc::new(ClosedResource::default());
    lifecycle.register_fail_closed(Arc::clone(&resource) as Arc<dyn FailClosed>);

    screen.locked.store(true, Ordering::Release);

    assert!(
        wait_for(|| !resource.reasons().is_empty()),
        "the lock watchdog should have closed the resource"
    );
    assert_eq!(resource.reasons(), vec![LifecycleFailure::ScreenLocked]);
    let _ = fs::remove_dir_all(&dir);
}
