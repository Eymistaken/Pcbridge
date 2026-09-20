use std::fs;
use std::io;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use pcbridge_native::lifecycle::{FailClosed, LifecycleFailure};
use pcbridge_native::platform::linux::input::{
    Pointer, PointerClock, PointerConfig, PointerDevice, PointerError, PointerEvent,
    PointerGeometry, SystemPointerClock, supported_pointer_buttons,
    supported_pointer_relative_axes,
};
use serde_json::Value;

const FIXTURE: &str = include_str!("../../../../tests/fixtures/native/input_events.json");
static NEXT_FIXTURE: AtomicU64 = AtomicU64::new(0);

#[derive(Clone)]
struct ManualClock {
    monotonic: Arc<Mutex<Duration>>,
    unix: Arc<Mutex<f64>>,
    events: Arc<Mutex<Vec<Value>>>,
}

impl PointerClock for ManualClock {
    fn monotonic(&self) -> Duration {
        *self.monotonic.lock().unwrap()
    }

    fn unix_time(&self) -> f64 {
        *self.unix.lock().unwrap()
    }

    fn sleep(&self, duration: Duration) {
        self.events
            .lock()
            .unwrap()
            .push(serde_json::json!(["sleep", duration.as_secs_f64()]));
        *self.monotonic.lock().unwrap() += duration;
        *self.unix.lock().unwrap() += duration.as_secs_f64();
    }
}

impl ManualClock {
    fn advance(&self, duration: Duration) {
        *self.monotonic.lock().unwrap() += duration;
        *self.unix.lock().unwrap() += duration.as_secs_f64();
    }
}

#[derive(Clone, Default)]
struct RecordingDevice {
    events: Arc<Mutex<Vec<Value>>>,
    fail_next: Arc<AtomicBool>,
}

impl PointerDevice for RecordingDevice {
    fn emit(&mut self, events: &[PointerEvent]) -> io::Result<()> {
        if self.fail_next.swap(false, Ordering::AcqRel) {
            return Err(io::Error::other("fixture device failure"));
        }
        let mut output = self.events.lock().unwrap();
        for event in events {
            output.push(serde_json::json!([
                event.event_type_name(),
                event.code_name(),
                event.value()
            ]));
        }
        output.push(serde_json::json!(["SYN"]));
        Ok(())
    }
}

fn fixture() -> Value {
    serde_json::from_str(FIXTURE).expect("golden input fixture should be valid")
}

fn fixture_root() -> PathBuf {
    let path = std::env::temp_dir().join(format!(
        "pcbridge-pointer-contract-{}-{}",
        std::process::id(),
        NEXT_FIXTURE.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir_all(&path).unwrap();
    path
}

fn expected_events(name: &str) -> Vec<Value> {
    fixture()["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|case| case["name"] == name)
        .unwrap()["expect"]["events"]
        .as_array()
        .unwrap()
        .clone()
}

fn pointer(
    state_file: PathBuf,
    unix_time: f64,
) -> (
    Pointer<RecordingDevice, ManualClock>,
    RecordingDevice,
    ManualClock,
) {
    let device = RecordingDevice::default();
    let clock = ManualClock {
        monotonic: Arc::new(Mutex::new(Duration::ZERO)),
        unix: Arc::new(Mutex::new(unix_time)),
        events: Arc::clone(&device.events),
    };
    let config = PointerConfig {
        speed: 5000.0,
        max_ms: 500.0,
        step: Duration::from_millis(8),
        hold_max: Duration::from_secs(120),
        drag_min_steps: 10,
    };
    let pointer = Pointer::new(
        device.clone(),
        clock.clone(),
        PointerGeometry::new(3840, 1080).unwrap(),
        config,
        Some(state_file),
    );
    (pointer, device, clock)
}

#[test]
fn virtual_pointer_capabilities_match_the_python_golden_fixture() {
    let fixture = fixture();
    let expected = &fixture["devices"]["pointer"];
    let geometry = PointerGeometry::new(3840, 1080).unwrap();

    assert_eq!(geometry.max_x(), expected["absolute"]["ABS_X"]["max"]);
    assert_eq!(geometry.max_y(), expected["absolute"]["ABS_Y"]["max"]);
    assert_eq!(
        supported_pointer_buttons(),
        expected["keys"]
            .as_array()
            .unwrap()
            .iter()
            .map(|value| value.as_str().unwrap().to_owned())
            .collect::<Vec<_>>()
    );
    assert_eq!(
        supported_pointer_relative_axes(),
        expected["relative"]
            .as_array()
            .unwrap()
            .iter()
            .map(|value| value.as_str().unwrap().to_owned())
            .collect::<Vec<_>>()
    );
    assert!(
        !supported_pointer_buttons()
            .iter()
            .any(|name| { matches!(name.as_str(), "BTN_TOUCH" | "BTN_TOOL_PEN") })
    );
}

#[test]
fn smooth_move_events_and_persisted_position_match_the_golden_fixture() {
    let root = fixture_root();
    let state_file = root.join("pointer.json");
    fs::write(&state_file, r#"{"x":100,"y":100,"t":1000.0}"#).unwrap();
    let (pointer, device, _clock) = pointer(state_file.clone(), 1000.0);

    assert_eq!(pointer.move_to(1060, 100, None, 1).unwrap(), (1060, 100));
    assert_eq!(
        *device.events.lock().unwrap(),
        expected_events("pointer_path")
    );
    assert_eq!(pointer.position(), Some((1060, 100)));
    let saved: Value = serde_json::from_slice(&fs::read(&state_file).unwrap()).unwrap();
    assert_eq!(
        (saved["x"].as_i64(), saved["y"].as_i64()),
        (Some(1060), Some(100))
    );

    pointer.close().unwrap();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn double_click_events_match_the_golden_fixture() {
    let root = fixture_root();
    let state_file = root.join("pointer.json");
    fs::write(&state_file, r#"{"x":500,"y":500,"t":1000.0}"#).unwrap();
    let (pointer, device, _clock) = pointer(state_file, 1000.0);

    pointer.click("left", 2).unwrap();
    assert_eq!(
        *device.events.lock().unwrap(),
        expected_events("double_click")
    );
    assert_eq!(pointer.position(), Some((500, 500)));

    pointer.close().unwrap();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn vertical_and_horizontal_scroll_events_match_the_golden_fixture() {
    let vertical_root = fixture_root();
    let (vertical, vertical_device, _clock) = pointer(vertical_root.join("pointer.json"), 1000.0);
    vertical.scroll(3, false).unwrap();
    assert_eq!(
        *vertical_device.events.lock().unwrap(),
        expected_events("scroll_up")
    );
    vertical.close().unwrap();
    fs::remove_dir_all(vertical_root).unwrap();

    let horizontal_root = fixture_root();
    let (horizontal, horizontal_device, _clock) =
        pointer(horizontal_root.join("pointer.json"), 1000.0);
    horizontal.scroll(-2, true).unwrap();
    assert_eq!(
        *horizontal_device.events.lock().unwrap(),
        expected_events("scroll_left")
    );
    horizontal.close().unwrap();
    fs::remove_dir_all(horizontal_root).unwrap();
}

#[test]
fn held_button_state_and_explicit_release_match_the_python_order() {
    let root = fixture_root();
    let (pointer, device, _clock) = pointer(root.join("pointer.json"), 1000.0);

    pointer.mouse_down("middle").unwrap();
    pointer.mouse_down("left").unwrap();
    assert_eq!(pointer.held(), vec!["left", "middle"]);
    assert_eq!(pointer.release_all().unwrap(), vec!["left", "middle"]);
    assert!(pointer.held().is_empty());
    assert_eq!(
        *device.events.lock().unwrap(),
        vec![
            serde_json::json!(["EV_KEY", "BTN_MIDDLE", 1]),
            serde_json::json!(["SYN"]),
            serde_json::json!(["EV_KEY", "BTN_LEFT", 1]),
            serde_json::json!(["SYN"]),
            serde_json::json!(["EV_KEY", "BTN_LEFT", 0]),
            serde_json::json!(["EV_KEY", "BTN_MIDDLE", 0]),
            serde_json::json!(["SYN"]),
        ]
    );

    pointer.close().unwrap();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn drag_events_match_the_golden_fixture() {
    let root = fixture_root();
    let state_file = root.join("pointer.json");
    fs::write(&state_file, r#"{"x":10,"y":10,"t":1000.0}"#).unwrap();
    let (pointer, device, _clock) = pointer(state_file, 1000.0);

    pointer.drag(10, 10, 210, 10, "left").unwrap();
    assert_eq!(*device.events.lock().unwrap(), expected_events("drag"));
    assert_eq!(pointer.position(), Some((210, 10)));
    assert!(pointer.held().is_empty());

    pointer.close().unwrap();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn monotonic_hold_timer_releases_a_button_without_another_action() {
    let root = fixture_root();
    let (pointer, device, clock) = pointer(root.join("pointer.json"), 1000.0);
    pointer.mouse_down("left").unwrap();

    clock.advance(Duration::from_secs(119));
    pointer.notify_clock_changed();
    std::thread::sleep(Duration::from_millis(10));
    assert_eq!(pointer.held(), vec!["left"]);

    clock.advance(Duration::from_secs(1));
    pointer.notify_clock_changed();
    let deadline = std::time::Instant::now() + Duration::from_millis(250);
    while device.events.lock().unwrap().len() < 4 {
        assert!(
            std::time::Instant::now() < deadline,
            "timer did not release the pointer button"
        );
        std::thread::yield_now();
    }
    assert!(pointer.held().is_empty());
    assert_eq!(pointer.take_auto_released(), vec!["left"]);
    assert!(pointer.take_auto_released().is_empty());

    pointer.close().unwrap();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn revoke_interrupts_a_long_smooth_move_without_waiting_for_the_path() {
    let root = fixture_root();
    let device = RecordingDevice::default();
    let config = PointerConfig {
        speed: 200.0,
        max_ms: 5_000.0,
        step: Duration::from_millis(8),
        hold_max: Duration::from_secs(120),
        drag_min_steps: 10,
    };
    let pointer = Arc::new(Pointer::new(
        device.clone(),
        SystemPointerClock::default(),
        PointerGeometry::new(3840, 1080).unwrap(),
        config,
        Some(root.join("pointer.json")),
    ));
    pointer.move_to(0, 0, Some(false), 1).unwrap();
    device.events.lock().unwrap().clear();

    let moving = Arc::clone(&pointer);
    let worker = thread::spawn(move || moving.move_to(3839, 1000, Some(true), 1));
    let event_deadline = Instant::now() + Duration::from_millis(250);
    while device.events.lock().unwrap().len() < 6 {
        assert!(Instant::now() < event_deadline, "smooth move did not start");
        thread::yield_now();
    }

    let started = Instant::now();
    pointer.close_fail_closed(LifecycleFailure::Revoked);
    assert!(
        started.elapsed() < Duration::from_millis(250),
        "revoke waited for the remaining smooth path"
    );
    assert!(matches!(worker.join().unwrap(), Err(PointerError::Closed)));

    fs::remove_dir_all(root).unwrap();
}

#[test]
fn an_emit_error_attempts_button_cleanup_and_closes_the_pointer() {
    let root = fixture_root();
    let (pointer, device, _clock) = pointer(root.join("pointer.json"), 1000.0);
    pointer.mouse_down("left").unwrap();
    device.fail_next.store(true, Ordering::Release);

    assert!(pointer.scroll(1, false).is_err());
    assert!(pointer.is_closed());
    assert!(pointer.held().is_empty());
    assert!(
        device
            .events
            .lock()
            .unwrap()
            .contains(&serde_json::json!(["EV_KEY", "BTN_LEFT", 0]))
    );

    pointer.close().unwrap();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn first_move_clamps_and_explicit_smooth_false_teleports() {
    let first_root = fixture_root();
    let (first, first_device, _clock) = pointer(first_root.join("pointer.json"), 1000.0);
    assert_eq!(first.move_to(5000, -20, None, 1).unwrap(), (3839, 0));
    assert_eq!(
        *first_device.events.lock().unwrap(),
        expected_events("pointer_first_move_clamped")
    );
    first.close().unwrap();
    fs::remove_dir_all(first_root).unwrap();

    let teleport_root = fixture_root();
    let state_file = teleport_root.join("pointer.json");
    fs::write(&state_file, r#"{"x":500,"y":500,"t":1000.0}"#).unwrap();
    let (teleport, teleport_device, _clock) = pointer(state_file, 1000.0);
    assert_eq!(
        teleport.move_to(3000, 900, Some(false), 1).unwrap(),
        (3000, 900)
    );
    assert_eq!(
        *teleport_device.events.lock().unwrap(),
        expected_events("pointer_teleport")
    );
    teleport.close().unwrap();
    fs::remove_dir_all(teleport_root).unwrap();
}

#[test]
fn opening_a_device_preserves_fresh_state_and_ignores_stale_state() {
    let fresh_root = fixture_root();
    let fresh_file = fresh_root.join("pointer.json");
    let fresh = br#"{"x":700,"y":300,"t":1000.0}"#;
    fs::write(&fresh_file, fresh).unwrap();
    let (fresh_pointer, _device, _clock) = pointer(fresh_file.clone(), 1000.0);
    assert_eq!(fresh_pointer.position(), Some((700, 300)));
    assert_eq!(fs::read(&fresh_file).unwrap(), fresh);
    fresh_pointer.close().unwrap();
    fs::remove_dir_all(fresh_root).unwrap();

    let stale_root = fixture_root();
    let stale_file = stale_root.join("pointer.json");
    let stale = br#"{"x":700,"y":300,"t":699.0}"#;
    fs::write(&stale_file, stale).unwrap();
    let (stale_pointer, _device, _clock) = pointer(stale_file.clone(), 1000.0);
    assert_eq!(stale_pointer.position(), None);
    assert_eq!(fs::read(&stale_file).unwrap(), stale);
    stale_pointer.close().unwrap();
    fs::remove_dir_all(stale_root).unwrap();
}

#[test]
fn revoke_explicitly_releases_held_buttons_and_closes_the_pointer() {
    let root = fixture_root();
    let (pointer, device, _clock) = pointer(root.join("pointer.json"), 1000.0);
    pointer.mouse_down("left").unwrap();

    pointer.close_fail_closed(LifecycleFailure::Revoked);

    assert!(pointer.is_closed());
    assert!(pointer.held().is_empty());
    assert!(
        device
            .events
            .lock()
            .unwrap()
            .contains(&serde_json::json!(["EV_KEY", "BTN_LEFT", 0]))
    );
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn an_absolute_move_after_external_motion_steps_aside_first() {
    // MEASURED 2026-09-20 on the real desktop: after the relative device
    // carried the cursor from 960 to 1052, sending ABS_X=960 from this device
    // did nothing -- the kernel treats a repeated absolute value as no
    // change, and the pointer stayed where the nudge left it. 961 worked, and
    // 960 worked after it. The Python fixture pins the same sequence in
    // `move_by_then_absolute`.
    let root = fixture_root();
    let (pointer, device, _clock) = pointer(root.join("pointer.json"), 1_000.0);
    pointer.move_to(100, 100, Some(false), 1).unwrap();
    device.events.lock().unwrap().clear();

    pointer.note_external_motion();
    assert_eq!(pointer.position(), None, "the recorded position is dropped");
    pointer.move_to(100, 100, Some(false), 1).unwrap();

    let events = device.events.lock().unwrap().clone();
    let absolute: Vec<_> = events
        .iter()
        .filter(|event| event[0] == "EV_ABS")
        .cloned()
        .collect();
    assert_eq!(
        absolute,
        vec![
            serde_json::json!(["EV_ABS", "ABS_X", 99]),
            serde_json::json!(["EV_ABS", "ABS_Y", 100]),
            serde_json::json!(["EV_ABS", "ABS_X", 100]),
            serde_json::json!(["EV_ABS", "ABS_Y", 100]),
        ]
    );
}

#[test]
fn a_plain_absolute_move_does_not_step_aside() {
    // The resync is paid only after external motion; the ordinary path must
    // not grow an extra event.
    let root = fixture_root();
    let (pointer, device, _clock) = pointer(root.join("pointer.json"), 1_000.0);
    pointer.move_to(100, 100, Some(false), 1).unwrap();
    device.events.lock().unwrap().clear();
    pointer.move_to(200, 200, Some(false), 1).unwrap();

    let events = device.events.lock().unwrap().clone();
    let syns = events.iter().filter(|event| event[0] == "SYN").count();
    assert_eq!(syns, 1);
}
