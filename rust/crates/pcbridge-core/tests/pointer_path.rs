use pcbridge_core::input::{global_to_device, move_path};
use serde_json::Value;

const FIXTURE: &str = include_str!("../../../../tests/fixtures/native/input_events.json");

fn expected_path(name: &str) -> Vec<(i32, i32)> {
    let fixture: Value = serde_json::from_str(FIXTURE).expect("input fixture should be valid");
    let events = fixture["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|case| case["name"] == name)
        .unwrap()["expect"]["events"]
        .as_array()
        .unwrap();
    let mut points = Vec::new();
    let mut x = None;
    for event in events {
        let Some(parts) = event.as_array() else {
            continue;
        };
        match parts.get(1).and_then(Value::as_str) {
            Some("ABS_X") => x = parts.get(2).and_then(Value::as_i64),
            Some("ABS_Y") => {
                let y = parts.get(2).and_then(Value::as_i64).unwrap();
                points.push((x.take().unwrap() as i32, y as i32));
            }
            _ => {}
        }
    }
    points
}

#[test]
fn smooth_pointer_path_matches_the_python_golden_fixture() {
    assert_eq!(
        move_path(100, 100, 1060, 100, 5000.0, 500.0, 0.008, 1),
        expected_path("pointer_path")
    );
}

#[test]
fn zero_speed_teleports_but_drag_minimum_still_interpolates() {
    assert_eq!(
        move_path(10, 10, 210, 10, 0.0, 500.0, 0.008, 1),
        vec![(210, 10)]
    );
    assert_eq!(
        move_path(10, 10, 210, 10, 0.0, 500.0, 0.008, 10),
        vec![
            (16, 10),
            (31, 10),
            (53, 10),
            (80, 10),
            (110, 10),
            (140, 10),
            (167, 10),
            (189, 10),
            (204, 10),
            (210, 10),
        ]
    );
}

#[test]
fn minimum_and_maximum_motion_durations_match_python_rounding() {
    assert_eq!(
        move_path(0, 0, 1000, 0, 100_000.0, 500.0, 0.008, 1).len(),
        8
    );
    assert_eq!(move_path(0, 0, 10_000, 0, 100.0, 500.0, 0.008, 1).len(), 62);
}

#[test]
fn global_coordinates_are_clamped_once_to_the_native_canvas() {
    assert_eq!(global_to_device(2500, 500, 3840, 1080), Some((2500, 500)));
    assert_eq!(global_to_device(5000, -20, 3840, 1080), Some((3839, 0)));
    assert_eq!(global_to_device(1, 1, 0, 1080), None);
}
