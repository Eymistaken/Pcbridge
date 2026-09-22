//! The layout matrix of pcbridge 2.0 (Step 6): fourteen layouts, both of
//! Mutter's layout modes, rotations, gaps and a negative origin.
//!
//! `tests/fixtures/native/layout_matrix.json` is resolved by
//! `tests/contracts/test_layout_matrix.py` as well, so a rule that drifts
//! between the two languages fails on one side only. The expected tables were
//! computed by hand. No D-Bus connection and no compositor.

use std::fs;
use std::path::PathBuf;

use pcbridge_core::display::{DisplayState, canvas_size, platform_origin, resolve, topology_id};
use serde::Deserialize;

#[derive(Debug, Deserialize)]
struct Fixture {
    layouts: Vec<Layout>,
}

#[derive(Debug, Deserialize)]
struct Layout {
    name: String,
    state: DisplayState,
    expect: Expectation,
}

#[derive(Debug, Deserialize)]
struct Expectation {
    monitors: Vec<ExpectedMonitor>,
    canvas: (u32, u32),
    platform_origin: (i32, i32),
    topology: String,
    gaps: Vec<(i32, i32)>,
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
    pixel_ratio: f64,
    transform: u32,
    primary: bool,
    serial: String,
    platform_x: i32,
    platform_y: i32,
    source_pixel_size: (u32, u32),
}

fn fixture() -> Fixture {
    let path: PathBuf = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../../tests/fixtures/native/layout_matrix.json");
    let raw = fs::read_to_string(&path)
        .unwrap_or_else(|err| panic!("fixture unreadable at {}: {err}", path.display()));
    serde_json::from_str(&raw).expect("fixture is not valid JSON for the shared schema")
}

#[test]
fn the_matrix_is_not_empty() {
    assert!(fixture().layouts.len() >= 12);
}

#[test]
fn every_layout_resolves_to_the_expected_table() {
    for layout in fixture().layouts {
        let monitors =
            resolve(&layout.state).unwrap_or_else(|err| panic!("{}: refused: {err}", layout.name));
        assert_eq!(
            monitors.len(),
            layout.expect.monitors.len(),
            "{}",
            layout.name
        );
        for (got, want) in monitors.iter().zip(&layout.expect.monitors) {
            let at = format!("{} / {}", layout.name, want.connector);
            assert_eq!(got.index, want.index, "{at}");
            assert_eq!(got.connector, want.connector, "{at}");
            assert_eq!((got.x, got.y), (want.x, want.y), "{at}");
            assert_eq!((got.width, got.height), (want.width, want.height), "{at}");
            assert!((got.scale - want.scale).abs() < 1e-9, "{at}");
            assert!((got.pixel_ratio() - want.pixel_ratio).abs() < 1e-9, "{at}");
            assert_eq!(got.transform, want.transform, "{at}");
            assert_eq!(got.primary, want.primary, "{at}");
            assert_eq!(got.serial, want.serial, "{at}");
            assert_eq!(
                (got.platform_x, got.platform_y),
                (want.platform_x, want.platform_y),
                "{at}"
            );
            assert_eq!(got.source_pixel_size(), want.source_pixel_size, "{at}");
        }
    }
}

#[test]
fn canvas_origin_and_topology_match_python() {
    for layout in fixture().layouts {
        let monitors = resolve(&layout.state).expect("resolves");
        assert_eq!(
            canvas_size(&monitors),
            layout.expect.canvas,
            "{}",
            layout.name
        );
        assert_eq!(
            platform_origin(&monitors),
            layout.expect.platform_origin,
            "{}",
            layout.name
        );
        assert_eq!(
            topology_id(&monitors),
            layout.expect.topology,
            "{}",
            layout.name
        );
    }
}

#[test]
fn gap_points_lie_on_no_monitor() {
    for layout in fixture().layouts {
        let monitors = resolve(&layout.state).expect("resolves");
        for (x, y) in &layout.expect.gaps {
            let hit = monitors.iter().any(|m| {
                *x >= m.x && *x < m.x + m.width as i32 && *y >= m.y && *y < m.y + m.height as i32
            });
            assert!(!hit, "{}: ({x}, {y}) is on a monitor", layout.name);
        }
    }
}
