//! The SECOND, relative pointer device against the Python golden fixture.
//!
//! The fixture is shared with `tests/contracts/test_input_contract.py`: both
//! implementations must produce the same events for the same call. Nothing
//! here opens /dev/uinput.

use std::io;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, Weak};
use std::time::Duration;

use pcbridge_core::input::{MOVE_BY_MAX, MOVE_BY_MAX_CHUNKS, relative_chunks};
use pcbridge_native::lifecycle::{FailClosed, LifecycleFailure};
use pcbridge_native::platform::linux::input::{
    PointerClock, PointerError, Relative, RelativeDevice, RelativeEvent,
    supported_relative_pointer_axes, supported_relative_pointer_buttons,
};
use serde_json::{Value, json};

const FIXTURE: &str = include_str!("../../../../tests/fixtures/native/input_events.json");
const STEP: Duration = Duration::from_millis(8);

fn fixture() -> Value {
    serde_json::from_str(FIXTURE).expect("the golden fixture parses")
}

fn case(name: &str) -> Value {
    fixture()["cases"]
        .as_array()
        .expect("cases is a list")
        .iter()
        .find(|case| case["name"] == name)
        .unwrap_or_else(|| panic!("the fixture has a {name} case"))
        .clone()
}

fn names(value: &Value) -> Vec<String> {
    value
        .as_array()
        .expect("a list of names")
        .iter()
        .map(|entry| entry.as_str().expect("a name").to_owned())
        .collect()
}

#[derive(Clone)]
struct RecordingClock {
    events: Arc<Mutex<Vec<Value>>>,
}

impl PointerClock for RecordingClock {
    fn monotonic(&self) -> Duration {
        Duration::ZERO
    }

    fn unix_time(&self) -> f64 {
        0.0
    }

    fn sleep(&self, duration: Duration) {
        self.events
            .lock()
            .unwrap()
            .push(json!(["sleep", duration.as_secs_f64()]));
    }
}

#[derive(Clone, Default)]
struct RecordingDevice {
    events: Arc<Mutex<Vec<Value>>>,
    fail_next: Arc<AtomicBool>,
}

impl RelativeDevice for RecordingDevice {
    fn emit(&mut self, events: &[RelativeEvent]) -> io::Result<()> {
        if self.fail_next.swap(false, Ordering::SeqCst) {
            return Err(io::Error::other("the device went away"));
        }
        let mut log = self.events.lock().unwrap();
        for event in events {
            log.push(json!(["EV_REL", event.code_name(), event.value()]));
        }
        log.push(json!(["SYN"]));
        Ok(())
    }
}

fn relative() -> (Relative<RecordingDevice, RecordingClock>, RecordingDevice) {
    let events = Arc::new(Mutex::new(Vec::new()));
    let device = RecordingDevice {
        events: Arc::clone(&events),
        fail_next: Arc::new(AtomicBool::new(false)),
    };
    let clock = RecordingClock { events };
    (Relative::new(device.clone(), clock, STEP), device)
}

#[test]
fn relative_device_capabilities_match_the_python_golden_fixture() {
    let expected = fixture()["devices"]["pointer_relative"].clone();
    assert_eq!(expected["name"], "pcbridge-pointer-rel");
    assert_eq!(
        supported_relative_pointer_axes(),
        names(&expected["relative"])
    );
    assert_eq!(
        supported_relative_pointer_buttons(),
        names(&expected["keys"])
    );
    // No absolute axis: this device has no coordinate space at all, which is
    // why a monitor hotplug cannot invalidate it.
    assert_eq!(names(&expected["event_types"]), vec!["EV_KEY", "EV_REL"]);
}

#[test]
fn relative_axes_did_not_leak_onto_the_absolute_device() {
    // The BTN_TOUCH lesson, machine-checked: the absolute pointer's measured
    // <=1 px accuracy depends on how udev classifies it, so it keeps exactly
    // the wheel axes it was measured with.
    let absolute = fixture()["devices"]["pointer"].clone();
    assert_eq!(
        names(&absolute["relative"]),
        vec!["REL_WHEEL", "REL_HWHEEL"]
    );
}

#[test]
fn every_relative_case_replays_to_its_golden_events() {
    for name in ["move_by_small", "move_by_chunked"] {
        let case = case(name);
        let call = &case["calls"][0];
        assert_eq!(call["op"], "move_by");
        let dx = call["args"][0].as_i64().unwrap() as i32;
        let dy = call["args"][1].as_i64().unwrap() as i32;

        let (relative, device) = relative();
        let sent = relative.move_by(dx, dy).expect("the nudge is sent");
        assert_eq!(sent, (dx, dy), "{name}: the whole delta is sent");
        let recorded = device.events.lock().unwrap().clone();
        assert_eq!(
            Value::from(recorded),
            case["expect"]["events"],
            "{name}: events differ from the Python golden fixture"
        );
    }
}

#[test]
fn the_relative_device_can_never_emit_a_button() {
    // Structural, not conventional: `RelativeEvent` has no key variant, so the
    // only thing this test can check is that the fixture agrees.
    for name in ["move_by_small", "move_by_chunked"] {
        let events = case(name)["expect"]["events"].as_array().unwrap().clone();
        assert!(events.iter().all(|event| event[0] != "EV_KEY"), "{name}");
    }
}

#[test]
fn chunking_preserves_the_total_and_respects_both_caps() {
    for (dx, dy) in [
        (40, -16),
        (200, 0),
        (0, 300),
        (-137, 59),
        (1, 1),
        (MOVE_BY_MAX, -MOVE_BY_MAX),
    ] {
        let chunks = relative_chunks(dx, dy);
        let total = chunks
            .iter()
            .fold((0, 0), |sum, chunk| (sum.0 + chunk.0, sum.1 + chunk.1));
        assert_eq!(total, (dx, dy), "chunking changed the total");
    }
    assert!(relative_chunks(0, 0).is_empty());

    let clamped = relative_chunks(99_999, -99_999);
    let total = clamped
        .iter()
        .fold((0, 0), |sum, chunk| (sum.0 + chunk.0, sum.1 + chunk.1));
    assert_eq!(total, (MOVE_BY_MAX, -MOVE_BY_MAX));
    assert!(clamped.len() <= MOVE_BY_MAX_CHUNKS);
}

/// A clock that revokes the grant after a fixed number of chunk gaps.
///
/// Deterministic on purpose: a watcher thread racing the nudge would pass
/// whether or not the lock is released between chunks, which is the one thing
/// this test exists to prove.
type RevokeTarget = Arc<Mutex<Option<Weak<Relative<RecordingDevice, RevokingClock>>>>>;

#[derive(Clone)]
struct RevokingClock {
    events: Arc<Mutex<Vec<Value>>>,
    target: RevokeTarget,
    revoke_after: usize,
    slept: Arc<AtomicUsize>,
}

impl PointerClock for RevokingClock {
    fn monotonic(&self) -> Duration {
        Duration::ZERO
    }

    fn unix_time(&self) -> f64 {
        0.0
    }

    fn sleep(&self, duration: Duration) {
        self.events
            .lock()
            .unwrap()
            .push(json!(["sleep", duration.as_secs_f64()]));
        if self.slept.fetch_add(1, Ordering::SeqCst) + 1 == self.revoke_after
            && let Some(relative) = self
                .target
                .lock()
                .unwrap()
                .as_ref()
                .and_then(std::sync::Weak::upgrade)
        {
            relative.close_fail_closed(LifecycleFailure::Revoked);
        }
    }
}

#[test]
fn a_revoke_stops_a_long_nudge_partway() {
    // The lock is taken per chunk precisely so a revoke can land mid-nudge.
    const REVOKE_AFTER: usize = 3;
    let events = Arc::new(Mutex::new(Vec::new()));
    let device = RecordingDevice {
        events: Arc::clone(&events),
        fail_next: Arc::new(AtomicBool::new(false)),
    };
    let clock = RevokingClock {
        events: Arc::clone(&events),
        target: Arc::new(Mutex::new(None)),
        revoke_after: REVOKE_AFTER,
        slept: Arc::new(AtomicUsize::new(0)),
    };
    let relative = Arc::new(Relative::new(device.clone(), clock.clone(), STEP));
    *clock.target.lock().unwrap() = Some(Arc::downgrade(&relative));

    let outcome = relative.move_by(MOVE_BY_MAX, 0);
    assert!(
        matches!(outcome, Err(PointerError::Closed)),
        "a revoked nudge reports Closed, got {outcome:?}"
    );
    assert!(relative.is_closed());

    // A chunk is emitted, then the gap is slept. The revoke lands in the
    // third gap, so exactly three chunks reached the device and the fourth
    // never did.
    let recorded = events.lock().unwrap().clone();
    let syns = recorded.iter().filter(|event| event[0] == "SYN").count();
    assert_eq!(syns, REVOKE_AFTER, "the rest of the nudge still went out");
    assert!(
        relative_chunks(MOVE_BY_MAX, 0).len() > syns,
        "the test delta must be long enough to be interrupted"
    );
}

#[test]
fn a_write_error_gives_the_device_up() {
    let (relative, device) = relative();
    device.fail_next.store(true, Ordering::SeqCst);
    let outcome = relative.move_by(10, 0);
    assert!(matches!(outcome, Err(PointerError::Device(_))));
    assert!(relative.is_closed(), "a failed write must fail closed");
    assert!(matches!(relative.move_by(10, 0), Err(PointerError::Closed)));
}
