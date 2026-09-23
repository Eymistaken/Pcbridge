//! Display snapshot parity against the Python resolver.
//!
//! Both sides read `tests/fixtures/native/display_state_cases.json` and feed it
//! to their own production resolver. Only the transport differs -- busctl JSON
//! in Python, zbus here -- so a divergence in the rules that matter (current
//! mode, rotation, fractional scale, rounding, ordering, topology) fails on one
//! side only.
//!
//! No D-Bus connection and no compositor: the fixture is the input.

use std::fs;
use std::path::PathBuf;

use pcbridge_core::display::{DisplayState, canvas_size, resolve, topology_id};
use serde::Deserialize;

#[derive(Debug, Deserialize)]
struct Fixture {
    cases: Vec<AcceptedCase>,
    rejected: Vec<RejectedCase>,
}

#[derive(Debug, Deserialize)]
struct AcceptedCase {
    name: String,
    note: String,
    state: DisplayState,
    expect: Expectation,
}

#[derive(Debug, Deserialize)]
struct Expectation {
    monitors: Vec<ExpectedMonitor>,
    canvas: (u32, u32),
    topology: String,
}

#[derive(Debug, Deserialize)]
struct ExpectedMonitor {
    index: u32,
    connector: String,
    x: i32,
    y: i32,
    width: u32,
    height: u32,
    scale: f64,
    transform: u32,
    primary: bool,
    serial: String,
}

#[derive(Debug, Deserialize)]
struct RejectedCase {
    name: String,
    note: String,
    state: DisplayState,
}

fn fixture() -> Fixture {
    let path: PathBuf = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../../tests/fixtures/native/display_state_cases.json");
    let raw = fs::read_to_string(&path)
        .unwrap_or_else(|err| panic!("fixture unreadable at {}: {err}", path.display()));
    serde_json::from_str(&raw).expect("fixture is not valid JSON for the shared schema")
}

#[test]
fn accepted_cases_resolve_to_the_expected_table() {
    for case in fixture().cases {
        let monitors = resolve(&case.state)
            .unwrap_or_else(|err| panic!("{} should resolve: {err} ({})", case.name, case.note));
        assert_eq!(
            monitors.len(),
            case.expect.monitors.len(),
            "{}: monitor count",
            case.name
        );
        for (got, want) in monitors.iter().zip(case.expect.monitors.iter()) {
            assert_eq!(got.index, want.index, "{}: index", case.name);
            assert_eq!(got.connector, want.connector, "{}: connector", case.name);
            assert_eq!(got.x, want.x, "{}: x", case.name);
            assert_eq!(got.y, want.y, "{}: y", case.name);
            assert_eq!(got.width, want.width, "{}: width", case.name);
            assert_eq!(got.height, want.height, "{}: height", case.name);
            assert!(
                (got.scale - want.scale).abs() < f64::EPSILON,
                "{}: scale {} != {}",
                case.name,
                got.scale,
                want.scale
            );
            assert_eq!(got.transform, want.transform, "{}: transform", case.name);
            assert_eq!(got.primary, want.primary, "{}: primary", case.name);
            assert_eq!(got.serial, want.serial, "{}: serial", case.name);
        }
    }
}

#[test]
fn canvas_size_matches_python() {
    for case in fixture().cases {
        let monitors = resolve(&case.state).expect("case resolves");
        assert_eq!(canvas_size(&monitors), case.expect.canvas, "{}", case.name);
    }
}

#[test]
fn topology_id_is_the_shared_canonical_string() {
    for case in fixture().cases {
        let monitors = resolve(&case.state).expect("case resolves");
        assert_eq!(
            topology_id(&monitors),
            case.expect.topology,
            "{}",
            case.name
        );
    }
}

#[test]
fn rejected_cases_are_refused_instead_of_guessed() {
    for case in fixture().rejected {
        assert!(
            resolve(&case.state).is_err(),
            "{} must be refused: {}",
            case.name,
            case.note
        );
    }
}

#[test]
fn topology_id_ignores_connector_names() {
    // Measured 2026-09-12 on the target machine: connectors went from DP-1/DP-2
    // to DP-3/DP-4 with the geometry untouched. A name-derived identity would
    // claim the layout changed every time that happens.
    let mut fixtures = fixture();
    let case = fixtures.cases.remove(0);
    let baseline = topology_id(&resolve(&case.state).expect("case resolves"));

    let mut renamed = case.state;
    for physical in &mut renamed.physical {
        physical.connector = format!("{}-renamed", physical.connector);
    }
    for logical in &mut renamed.logical {
        for connector in &mut logical.connectors {
            *connector = format!("{connector}-renamed");
        }
    }
    assert_eq!(
        topology_id(&resolve(&renamed).expect("renamed case resolves")),
        baseline
    );
}

#[test]
fn topology_id_changes_when_a_monitor_moves() {
    let mut fixtures = fixture();
    let case = fixtures.cases.remove(0);
    let baseline = topology_id(&resolve(&case.state).expect("case resolves"));

    let mut moved = case.state;
    moved.logical[1].x += 1;
    assert_ne!(
        topology_id(&resolve(&moved).expect("moved case resolves")),
        baseline
    );
}

#[test]
fn topology_id_changes_on_a_half_turn_that_keeps_the_size() {
    let mut fixtures = fixture();
    let case = fixtures.cases.remove(0);
    let baseline = topology_id(&resolve(&case.state).expect("case resolves"));

    let mut turned = case.state;
    turned.logical[0].transform = 2; // 180 degrees: same size, different mapping
    let monitors = resolve(&turned).expect("turned case resolves");
    assert_eq!((monitors[0].width, monitors[0].height), (1920, 1080));
    assert_ne!(topology_id(&monitors), baseline);
}

/// KDE Plasma: `kscreen-doctor -j` must become the same neutral state the
/// Python adapter builds (`tests/fixtures/native/kscreen_cases.json`).
#[test]
fn kscreen_output_maps_to_the_shared_neutral_state() {
    use pcbridge_native::platform::linux::display::kscreen_state;

    let path: PathBuf = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../../tests/fixtures/native/kscreen_cases.json");
    let raw: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&path).expect("kscreen fixture")).unwrap();
    let cases = raw["cases"].as_array().expect("cases");
    assert!(!cases.is_empty());
    for case in cases {
        let name = case["name"].as_str().unwrap_or("?");
        let got = kscreen_state(&case["kscreen"]).unwrap_or_else(|err| panic!("{name}: {err}"));
        let want: DisplayState =
            serde_json::from_value(case["state"].clone()).expect("state in the shared schema");
        assert_eq!(got, want, "{name}");
        resolve(&got).unwrap_or_else(|err| panic!("{name} should resolve: {err}"));
    }
}
