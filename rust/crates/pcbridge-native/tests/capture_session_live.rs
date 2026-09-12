//! The real Mutter transport, against the compositor this machine is running.
//!
//! Skipped unless `PCBRIDGE_TEST_CAPTURE=1`, the same flag the Python live
//! capture tests use and for the same reason: it briefly opens a real screen
//! share, so GNOME shows the sharing indicator in the top bar while it runs. No
//! frame is taken, no file is written and no input is sent -- the session is
//! opened and closed again.
//!
//! `tests/capture_session.rs` covers the rules. This covers the one thing a fake
//! cannot: that the method names, argument signatures and signal shape are the
//! ones Mutter actually answers to.

use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Instant;

use pcbridge_native::lifecycle::LifecycleFailure;
use pcbridge_native::platform::linux::display::DisplayReader;
use pcbridge_native::platform::linux::session::{
    CaptureSession, CursorMode, MutterScreenCast, OpenOutcome, SessionGuard, SessionState,
    StartRequest,
};

/// The compositor does not consult pcbridge's grant; this test is the
/// deliberate, flag-gated caller that stands in for it.
struct FlaggedRun {
    checks: AtomicUsize,
}

impl SessionGuard for FlaggedRun {
    fn check(&self) -> Result<(), LifecycleFailure> {
        self.checks.fetch_add(1, Ordering::AcqRel);
        Ok(())
    }
}

fn enabled() -> bool {
    std::env::var("PCBRIDGE_TEST_CAPTURE").as_deref() == Ok("1")
}

#[test]
fn a_real_session_maps_every_connector_to_a_pipewire_node() {
    if !enabled() {
        eprintln!("skipped: set PCBRIDGE_TEST_CAPTURE=1 to open a real screen share");
        return;
    }

    let display = DisplayReader::connect().expect("Mutter DisplayConfig");
    let snapshot = display.snapshot().expect("display snapshot");
    let request = StartRequest {
        monitors: snapshot
            .monitors
            .iter()
            .map(|monitor| monitor.connector.clone())
            .collect(),
        cursor: CursorMode::Embedded,
        topology_id: snapshot.topology_id.clone(),
    };
    eprintln!(
        "monitors: {:?} topology: {}",
        request.monitors, request.topology_id
    );

    let guard = FlaggedRun {
        checks: AtomicUsize::new(0),
    };
    let mut session = CaptureSession::new(MutterScreenCast::connect().expect("Mutter ScreenCast"));

    let started = Instant::now();
    let outcome = session.open(&request, &guard).expect("open a real session");
    let open_ms = started.elapsed().as_secs_f64() * 1000.0;

    assert_eq!(outcome, OpenOutcome::Opened);
    assert_eq!(session.state(), SessionState::Ready);
    let mut nodes = Vec::new();
    for connector in &request.monitors {
        let node = session
            .node_for(connector)
            .unwrap_or_else(|| panic!("{connector} should have a PipeWire node"));
        assert!(node > 0, "{connector} reported node 0");
        nodes.push(node);
    }
    let distinct: std::collections::BTreeSet<u32> = nodes.iter().copied().collect();
    assert_eq!(
        distinct.len(),
        nodes.len(),
        "two connectors must not share a node: {nodes:?}"
    );
    eprintln!("open: {open_ms:.1} ms  nodes: {nodes:?}");

    // Reusing must not go near the bus, even when the bus is real.
    let reused = Instant::now();
    assert_eq!(
        session.open(&request, &guard).expect("reuse"),
        OpenOutcome::Reused
    );
    eprintln!("reuse: {:.3} ms", reused.elapsed().as_secs_f64() * 1000.0);

    // The cursor is chosen when the stream is recorded, so changing it is a
    // recreate. Python measured ~113 ms for the same move.
    let mut hidden = request.clone();
    hidden.cursor = CursorMode::Hidden;
    let recreated = Instant::now();
    assert_eq!(
        session.open(&hidden, &guard).expect("cursor recreate"),
        OpenOutcome::Recreated
    );
    eprintln!(
        "cursor recreate: {:.1} ms",
        recreated.elapsed().as_secs_f64() * 1000.0
    );

    let stopped = Instant::now();
    assert_eq!(session.stop(), Ok(true));
    eprintln!("stop: {:.1} ms", stopped.elapsed().as_secs_f64() * 1000.0);
    assert_eq!(session.state(), SessionState::Closed);
    assert_eq!(session.node_for(&request.monitors[0]), None);
    assert_eq!(session.stop(), Ok(false));
}
