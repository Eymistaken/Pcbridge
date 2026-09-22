//! Canvas geometry parity for mixed scale, negative origin and gaps (Task 7.1).
//!
//! The layouts in `tests/fixtures/native/mixed_scale_cases.json` are read by
//! `tests/contracts/test_coordinate_v2.py` as well, so a rule that drifts
//! between the two languages fails on one side only. The expected values were
//! computed by hand: logical size is the mode divided by the scale, rounded
//! half away from zero, and the canvas always starts at (0, 0) whatever origin
//! the compositor reported.
//!
//! The target machine runs both monitors at scale 1.0, so the scaled layouts
//! here are not measured hardware. No D-Bus connection and no compositor.

use std::fs;
use std::path::PathBuf;

use pcbridge_core::display::{
    DisplayState, Monitor, canvas_size, platform_origin, resolve, topology_id,
};
use serde::Deserialize;

#[derive(Debug, Deserialize)]
struct Fixture {
    layouts: Vec<Layout>,
    canvas_ratio: Vec<CanvasRatio>,
}

#[derive(Debug, Deserialize)]
struct Layout {
    name: String,
    note: String,
    state: DisplayState,
    expect: Expectation,
}

#[derive(Debug, Deserialize)]
struct Expectation {
    monitors: Vec<ExpectedMonitor>,
    canvas: (u32, u32),
    platform_origin: (i32, i32),
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
    platform_x: i32,
    platform_y: i32,
    source_pixel_size: (u32, u32),
}

/// Only the entry that carries its own state is used here: the others name a
/// layout the Python side looks up, and the ratio itself is Python's job
/// (the single-image fallback path lives there).
#[derive(Debug, Deserialize)]
struct CanvasRatio {
    name: String,
    state: Option<DisplayState>,
    logical_canvas: Option<(u32, u32)>,
}

fn fixture() -> Fixture {
    let path: PathBuf = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../../tests/fixtures/native/mixed_scale_cases.json");
    let raw = fs::read_to_string(&path)
        .unwrap_or_else(|err| panic!("fixture unreadable at {}: {err}", path.display()));
    serde_json::from_str(&raw).expect("fixture is not valid JSON for the shared schema")
}

#[test]
fn every_layout_resolves_to_the_expected_table() {
    for layout in fixture().layouts {
        let monitors = resolve(&layout.state).unwrap_or_else(|err| {
            panic!("{} should resolve: {err} ({})", layout.name, layout.note)
        });
        assert_eq!(
            monitors.len(),
            layout.expect.monitors.len(),
            "{}: monitor count",
            layout.name
        );
        for (got, want) in monitors.iter().zip(layout.expect.monitors.iter()) {
            assert_eq!(got.index, want.index, "{}: index", layout.name);
            assert_eq!(got.connector, want.connector, "{}: connector", layout.name);
            assert_eq!((got.x, got.y), (want.x, want.y), "{}: canvas", layout.name);
            assert_eq!(
                (got.width, got.height),
                (want.width, want.height),
                "{}: logical size",
                layout.name
            );
            assert!(
                (got.scale - want.scale).abs() < f64::EPSILON,
                "{}: scale {} != {}",
                layout.name,
                got.scale,
                want.scale
            );
            assert_eq!(got.transform, want.transform, "{}: transform", layout.name);
            assert_eq!(got.primary, want.primary, "{}: primary", layout.name);
            assert_eq!(
                (got.platform_x, got.platform_y),
                (want.platform_x, want.platform_y),
                "{}: platform position",
                layout.name
            );
            assert_eq!(
                got.source_pixel_size(),
                want.source_pixel_size,
                "{}: raw pixel size",
                layout.name
            );
        }
    }
}

#[test]
fn the_canvas_starts_at_the_origin_whatever_the_compositor_said() {
    for layout in fixture().layouts {
        let monitors = resolve(&layout.state).expect("layout resolves");
        assert_eq!(
            canvas_size(&monitors),
            layout.expect.canvas,
            "{}: canvas size",
            layout.name
        );
        assert_eq!(
            platform_origin(&monitors),
            layout.expect.platform_origin,
            "{}: platform origin",
            layout.name
        );
        assert_eq!(
            monitors.iter().map(|m| m.x).min(),
            Some(0),
            "{}: canvas left",
            layout.name
        );
        assert_eq!(
            monitors.iter().map(|m| m.y).min(),
            Some(0),
            "{}: canvas top",
            layout.name
        );
    }
}

#[test]
fn topology_matches_the_string_python_builds() {
    for layout in fixture().layouts {
        let monitors = resolve(&layout.state).expect("layout resolves");
        assert_eq!(
            topology_id(&monitors),
            layout.expect.topology,
            "{}: topology",
            layout.name
        );
    }
}

#[test]
fn a_layout_that_only_moved_in_the_compositor_is_the_same_layout() {
    // The same two monitors with the compositor's origin 1920 units to the
    // left: nothing moved on screen, so no cached snapshot may be invalidated.
    let layouts = fixture().layouts;
    let here = layouts
        .iter()
        .find(|layout| layout.name == "this_machine_two_equal_monitors")
        .expect("fixture keeps this machine's layout");
    let shifted = layouts
        .iter()
        .find(|layout| layout.name == "negative_origin_is_normalized")
        .expect("fixture keeps the negative origin layout");
    assert_eq!(
        topology_id(&resolve(&here.state).expect("resolves")),
        topology_id(&resolve(&shifted.state).expect("resolves")),
    );
}

#[test]
fn canvas_size_is_the_bounding_box_of_whatever_it_is_given() {
    // `resolve` always hands back a normalized table, but this is a public
    // helper and a hand-built table may still carry the compositor's origin.
    let raw = vec![
        Monitor {
            index: 1,
            connector: "A".to_owned(),
            x: -1920,
            y: -100,
            width: 1920,
            height: 1080,
            scale: 1.0,
            primary: false,
            name: String::new(),
            transform: 0,
            serial: String::new(),
            platform_x: -1920,
            platform_y: -100,
            physical_layout: false,
        },
        Monitor {
            index: 2,
            connector: "B".to_owned(),
            x: 0,
            y: 0,
            width: 1920,
            height: 1080,
            scale: 1.0,
            primary: true,
            name: String::new(),
            transform: 0,
            serial: String::new(),
            platform_x: 0,
            platform_y: 0,
            physical_layout: false,
        },
    ];
    assert_eq!(canvas_size(&raw), (3840, 1180));
    assert_eq!(platform_origin(&raw), (-1920, -100));
}

#[test]
fn a_uniform_scale_layout_reports_its_raw_canvas() {
    // The single-image fallback needs the raw pixel canvas; each monitor's own
    // pixel size multiplies out to it when every scale is the same.
    let fixture = fixture();
    let case = fixture
        .canvas_ratio
        .iter()
        .find(|entry| entry.state.is_some())
        .unwrap_or_else(|| panic!("fixture keeps one layout with its own state"));
    let monitors = resolve(case.state.as_ref().expect("state")).expect("resolves");
    let logical = case.logical_canvas.expect("logical canvas");
    assert_eq!(canvas_size(&monitors), logical, "{}: logical", case.name);
    let raw: u32 = monitors.iter().map(|m| m.source_pixel_size().0).sum();
    assert_eq!(raw, logical.0 * 2, "{}: raw width", case.name);
}
