use std::io;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use pcbridge_native::lifecycle::{FailClosed, LifecycleFailure};
use pcbridge_native::platform::linux::input::{
    Keyboard, KeyboardClock, KeyboardDevice, KeyboardError, KeyboardEvent, supported_key_names,
};
use serde_json::{Value, json};

const FIXTURE: &str = include_str!("../../../../tests/fixtures/native/input_events.json");

#[derive(Clone, Default)]
struct ManualClock {
    now: Arc<Mutex<Duration>>,
    events: Arc<Mutex<Vec<Value>>>,
}

impl ManualClock {
    fn advance(&self, duration: Duration) {
        *self.now.lock().unwrap() += duration;
    }
}

impl KeyboardClock for ManualClock {
    fn now(&self) -> Duration {
        *self.now.lock().unwrap()
    }

    fn sleep(&self, duration: Duration) {
        self.events
            .lock()
            .unwrap()
            .push(json!(["sleep", duration.as_secs_f64()]));
        self.advance(duration);
    }
}

#[derive(Clone, Default)]
struct RecordingDevice {
    events: Arc<Mutex<Vec<Value>>>,
    fail_next: Arc<AtomicBool>,
}

impl KeyboardDevice for RecordingDevice {
    fn emit(&mut self, events: &[KeyboardEvent]) -> io::Result<()> {
        if self.fail_next.swap(false, Ordering::AcqRel) {
            return Err(io::Error::other("fixture device failure"));
        }
        let mut output = self.events.lock().unwrap();
        for event in events {
            output.push(json!(["EV_KEY", event.code_name(), event.value()]));
        }
        output.push(json!(["SYN"]));
        Ok(())
    }
}

fn fixture() -> Value {
    serde_json::from_str(FIXTURE).expect("golden input fixture should be valid")
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

fn keyboard() -> (
    Keyboard<RecordingDevice, ManualClock>,
    RecordingDevice,
    ManualClock,
) {
    let device = RecordingDevice::default();
    let clock = ManualClock {
        events: Arc::clone(&device.events),
        ..ManualClock::default()
    };
    (
        Keyboard::new(device.clone(), clock.clone(), Duration::from_secs(120)),
        device,
        clock,
    )
}

#[test]
fn virtual_keyboard_capabilities_match_the_python_golden_fixture() {
    let expected: Vec<String> = fixture()["devices"]["keyboard"]["keys"]
        .as_array()
        .unwrap()
        .iter()
        .map(|name| name.as_str().unwrap().to_owned())
        .collect();
    assert_eq!(supported_key_names(), expected);
}

#[test]
fn combo_events_match_the_golden_fixture() {
    let (keyboard, device, _clock) = keyboard();
    keyboard.key("ctrl+shift+t").unwrap();
    assert_eq!(*device.events.lock().unwrap(), expected_events("key_combo"));
}

#[test]
fn python_key_aliases_resolve_to_the_same_linux_codes() {
    for (alias, expected) in [
        ("control", "KEY_LEFTCTRL"),
        ("meta", "KEY_LEFTMETA"),
        ("win", "KEY_LEFTMETA"),
        ("cmd", "KEY_LEFTMETA"),
        ("enter", "KEY_ENTER"),
        ("esc", "KEY_ESC"),
        ("period", "KEY_DOT"),
    ] {
        let (keyboard, device, _clock) = keyboard();
        keyboard.key(alias).unwrap();
        assert_eq!(
            device.events.lock().unwrap()[0],
            json!(["EV_KEY", expected, 1])
        );
    }
}

#[test]
fn a_combo_does_not_release_an_already_held_modifier() {
    let (keyboard, device, _clock) = keyboard();
    keyboard.key_down("shift").unwrap();
    keyboard.key("shift+home").unwrap();
    assert_eq!(keyboard.held(), vec!["shift"]);
    assert_eq!(
        *device.events.lock().unwrap(),
        expected_events("key_with_held_modifier")
    );
}

#[test]
fn hold_and_release_match_the_golden_fixture() {
    let (keyboard, device, _clock) = keyboard();
    keyboard.key_down("ctrl+alt").unwrap();
    assert_eq!(keyboard.held(), vec!["ctrl", "alt"]);
    keyboard.key_up("ctrl+alt").unwrap();
    assert!(keyboard.held().is_empty());
    assert_eq!(
        *device.events.lock().unwrap(),
        expected_events("hold_release")
    );
}

#[test]
fn fake_monotonic_time_releases_a_hold_without_another_input_action() {
    let (keyboard, device, clock) = keyboard();
    keyboard.key_down("shift").unwrap();

    clock.advance(Duration::from_secs(119));
    keyboard.notify_clock_changed();
    std::thread::sleep(Duration::from_millis(10));
    assert_eq!(keyboard.held(), vec!["shift"]);

    clock.advance(Duration::from_secs(1));
    keyboard.notify_clock_changed();
    let deadline = std::time::Instant::now() + Duration::from_millis(250);
    while device.events.lock().unwrap().len() < 4 {
        assert!(
            std::time::Instant::now() < deadline,
            "timer did not release the key"
        );
        std::thread::yield_now();
    }

    assert_eq!(
        *device.events.lock().unwrap(),
        expected_events("hold_auto_release")
    );
    assert_eq!(keyboard.take_auto_released(), vec!["shift"]);
    assert!(keyboard.take_auto_released().is_empty());
    assert!(keyboard.held().is_empty());
}

#[test]
fn close_explicitly_releases_every_held_key() {
    let (keyboard, device, _clock) = keyboard();
    keyboard.key_down("ctrl+alt").unwrap();
    assert_eq!(keyboard.close().unwrap(), vec!["alt", "ctrl"]);
    assert!(keyboard.held().is_empty());
    let events = device.events.lock().unwrap();
    assert_eq!(events[3], json!(["EV_KEY", "KEY_LEFTALT", 0]));
    assert_eq!(events[4], json!(["EV_KEY", "KEY_LEFTCTRL", 0]));
    assert_eq!(events[5], json!(["SYN"]));
}

#[test]
fn revoke_closes_the_keyboard_after_explicitly_releasing_keys() {
    let (keyboard, device, _clock) = keyboard();
    keyboard.key_down("shift").unwrap();

    keyboard.close_fail_closed(LifecycleFailure::Revoked);

    assert!(keyboard.is_closed());
    assert_eq!(
        keyboard.key("a").unwrap_err().to_string(),
        KeyboardError::Closed.to_string()
    );
    assert!(keyboard.held().is_empty());
    assert!(
        device
            .events
            .lock()
            .unwrap()
            .contains(&json!(["EV_KEY", "KEY_LEFTSHIFT", 0]))
    );
}

#[test]
fn an_emit_error_attempts_cleanup_and_clears_held_state() {
    let (keyboard, device, _clock) = keyboard();
    keyboard.key_down("shift").unwrap();
    device.fail_next.store(true, Ordering::Release);
    assert!(keyboard.key("ctrl+a").is_err());
    assert!(keyboard.is_closed());
    assert!(matches!(keyboard.key("b"), Err(KeyboardError::Closed)));
    assert!(keyboard.held().is_empty());
    assert!(
        device
            .events
            .lock()
            .unwrap()
            .contains(&json!(["EV_KEY", "KEY_LEFTSHIFT", 0]))
    );
}
