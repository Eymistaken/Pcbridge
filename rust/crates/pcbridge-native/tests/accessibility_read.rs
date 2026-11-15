//! Accessibility reads against the shared fixture (Task 6.2).
//!
//! `tests/fixtures/native/accessibility_cases.json` holds the desktops and
//! the expected dumps; they were written for the Python AT-SPI helper
//! (removed after 2.8), and a case passes only when the native walk lists
//! exactly what that helper listed -- node for node, field for field.

use std::time::{Duration, Instant};

use pcbridge_native::accessibility::fixture::FixtureTree;
use pcbridge_native::accessibility::{
    self, DumpRequest, NodeInfo, ObjectRef, States, Tree, is_real_action, role_name,
};
use serde_json::Value;

const FIXTURE: &str = include_str!("../../../../tests/fixtures/native/accessibility_cases.json");

fn fixture() -> Value {
    serde_json::from_str(FIXTURE).expect("fixture is JSON")
}

fn tree(desktop: &str) -> FixtureTree {
    FixtureTree::from_fixture(&fixture(), desktop).expect("fixture desktop loads")
}

fn request(value: &Value) -> DumpRequest {
    DumpRequest {
        focus: None,
        target: value["target"].as_str().unwrap_or("focused").to_owned(),
        interactive_only: value["interactive_only"].as_bool().unwrap_or(true),
        max_nodes: value["max_nodes"]
            .as_u64()
            .map_or(accessibility::DEFAULT_MAX_NODES, |max| max as usize),
        deadline: None,
        deadline_seconds: 15,
    }
}

/// Every key the expectation names must match; the rest is not compared.
fn assert_subset(case: &str, got: &Value, want: &Value) {
    for (key, value) in want.as_object().expect("expectation is an object") {
        assert_eq!(&got[key], value, "{case}: {key}");
    }
}

#[test]
fn dump_cases_match_the_shared_fixture() {
    let fixture = fixture();
    let cases = fixture["dump_cases"].as_array().expect("dump cases");
    assert!(!cases.is_empty());
    for case in cases {
        let name = case["name"].as_str().expect("case name");
        let desktop = tree(case["desktop"].as_str().expect("desktop"));
        let expect = &case["expect"];
        let result = accessibility::dump(&desktop, &request(&case["request"]));
        if expect["ok"] == false {
            let error = result.expect_err(name);
            if let Some(code) = expect["code"].as_str() {
                assert_eq!(error.code, code, "{name}");
            }
            assert!(!error.message.is_empty(), "{name}: a refusal says why");
            continue;
        }
        let got = result.unwrap_or_else(|error| panic!("{name}: {error}"));
        let mut header = expect.clone();
        let nodes = header
            .as_object_mut()
            .expect("expectation is an object")
            .remove("nodes");
        assert_subset(name, &got, &header);
        if let Some(Value::Array(nodes)) = nodes {
            let listed = got["nodes"].as_array().expect("nodes");
            assert_eq!(listed.len(), nodes.len(), "{name}: node count");
            for (node, want) in listed.iter().zip(nodes) {
                assert_subset(name, node, &want);
            }
        }
    }
}

#[test]
fn window_cases_are_read_without_a_walk() {
    let fixture = fixture();
    for case in fixture["window_cases"].as_array().expect("window cases") {
        let name = case["name"].as_str().expect("case name");
        let desktop = tree(case["desktop"].as_str().expect("desktop"));
        let windows = accessibility::windows(&desktop).expect("windows");
        assert_eq!(windows["windows"], case["expect_windows"], "{name}");
        let expect = &case["expect_focused"];
        match accessibility::focused(&desktop) {
            Ok(got) => assert_subset(name, &got, expect),
            Err(error) => {
                assert_eq!(expect["ok"], false, "{name}: {error}");
                assert_eq!(error.code, expect["code"], "{name}");
            }
        }
    }
}

#[test]
fn every_listed_node_carries_its_object_path() {
    let desktop = tree("editor");
    let got = accessibility::dump(&desktop, &request(&serde_json::json!({}))).expect("dump");
    let refs: Vec<&str> = got["nodes"]
        .as_array()
        .expect("nodes")
        .iter()
        .map(|node| node["ref"].as_str().expect("ref"))
        .collect();
    assert!(refs.iter().all(|reference| !reference.is_empty()));
    let mut unique = refs.clone();
    unique.sort_unstable();
    unique.dedup();
    assert_eq!(unique.len(), refs.len());
}

/// Counts reads, to prove what a walk does not read.
struct Counting<T: Tree> {
    inner: T,
    reads: std::cell::Cell<usize>,
}

impl<T: Tree> Tree for Counting<T> {
    fn applications(&self) -> Result<Vec<ObjectRef>, accessibility::AccessibilityError> {
        self.inner.applications()
    }

    fn read(&self, nodes: &[ObjectRef]) -> Vec<Option<NodeInfo>> {
        self.reads.set(self.reads.get() + nodes.len());
        self.inner.read(nodes)
    }

    fn pid(&self, bus: &str) -> u32 {
        self.inner.pid(bus)
    }
}

#[test]
fn window_reads_stop_at_the_windows() {
    let desktop = Counting {
        inner: tree("editor"),
        reads: std::cell::Cell::new(0),
    };
    accessibility::windows(&desktop).expect("windows");
    // Two applications and their three windows, nothing below them.
    assert_eq!(desktop.reads.get(), 5);
    desktop.reads.set(0);
    accessibility::focused(&desktop).expect("focused");
    assert_eq!(desktop.reads.get(), 5);
}

#[test]
fn a_passed_deadline_is_a_timeout_not_a_partial_list() {
    let desktop = tree("editor");
    let mut late = request(&serde_json::json!({}));
    late.deadline = Some(Instant::now() - Duration::from_millis(1));
    let error = accessibility::dump(&desktop, &late).expect_err("deadline passed");
    assert_eq!(error.code, "TIMEOUT");
    assert!(
        error.message.contains("within 15 seconds"),
        "{}",
        error.message
    );
}

/// A tree deeper than the walk's limit: the nodes below it are not read.
struct Deep;

impl Tree for Deep {
    fn applications(&self) -> Result<Vec<ObjectRef>, accessibility::AccessibilityError> {
        Ok(vec![ObjectRef::new(":1.9", "/root")])
    }

    fn read(&self, nodes: &[ObjectRef]) -> Vec<Option<NodeInfo>> {
        nodes
            .iter()
            .map(|node| {
                let depth: usize = node
                    .path
                    .rsplit('/')
                    .next()
                    .and_then(|n| n.parse().ok())
                    .unwrap_or(0);
                let mut states = States::default();
                states.insert(accessibility::STATE_SHOWING);
                states.insert(accessibility::STATE_ACTIVE);
                Some(NodeInfo {
                    role: if depth == 0 {
                        "application"
                    } else {
                        "push button"
                    }
                    .to_owned(),
                    name: format!("n{depth}"),
                    states,
                    actions: vec!["click".to_owned()],
                    editable_text: false,
                    children: vec![Some(ObjectRef::new(":1.9", format!("/n/{}", depth + 1)))],
                })
            })
            .collect()
    }

    fn pid(&self, _bus: &str) -> u32 {
        1
    }
}

#[test]
fn depth_and_visit_limits_hold() {
    let got = accessibility::dump(
        &Deep,
        &DumpRequest {
            focus: None,
            target: "n0".to_owned(),
            interactive_only: true,
            max_nodes: 400,
            deadline: None,
            deadline_seconds: 15,
        },
    )
    .expect("dump");
    let nodes = got["nodes"].as_array().expect("nodes");
    // Depth 0 is the application (a container); depths 1..=100 are listed.
    assert_eq!(nodes.len(), accessibility::MAX_DEPTH);
    assert_eq!(
        nodes.last().expect("deepest")["depth"],
        accessibility::MAX_DEPTH
    );

    let got = accessibility::dump(
        &Deep,
        &DumpRequest {
            focus: None,
            target: "n0".to_owned(),
            interactive_only: true,
            max_nodes: 2,
            deadline: None,
            deadline_seconds: 15,
        },
    )
    .expect("dump");
    // Two listed, and the walk stopped after 2 * 25 visits.
    assert_eq!(got["nodes"].as_array().expect("nodes").len(), 2);
    assert_eq!(got["truncated"], true);
}

#[test]
fn roles_use_libatspis_table_not_the_toolkits_words() {
    // Measured 2026-09-19: GTK4's GetRoleName says "application" for its
    // window frame and "button" for a push button; libatspi says these.
    assert_eq!(role_name(23), Some("frame"));
    assert_eq!(role_name(39), Some("panel"));
    assert_eq!(role_name(40), Some("password text"));
    assert_eq!(role_name(43), Some("push button"));
    assert_eq!(role_name(75), Some("application"));
    assert_eq!(role_name(95), Some("document web"));
    // Extended roles and anything past the table are named by the toolkit.
    assert_eq!(role_name(70), None);
    assert_eq!(role_name(130), None);
    assert_eq!(role_name(4000), None);
}

#[test]
fn states_decode_both_words() {
    let states = States::from_words(&[(1 << 1) | (1 << 25), 1 << 1]);
    assert!(states.contains(accessibility::STATE_ACTIVE));
    assert!(states.contains(accessibility::STATE_SHOWING));
    assert!(states.contains(33)); // "required", in the second word
    assert!(!states.contains(7));
    assert_eq!(accessibility::state_bit("showing"), Some(25));
    assert_eq!(accessibility::state_bit("read-only"), Some(43));
}

#[test]
fn gactions_are_not_element_actions() {
    for real in ["click", "press", "activate", "doDefault"] {
        assert!(is_real_action(real), "{real}");
    }
    for noise in [
        "page.save-as",
        "clipboard.copy",
        "win.open",
        "window.minimize",
        "  ",
    ] {
        assert!(!is_real_action(noise), "{noise:?}");
    }
}

#[test]
fn the_window_managers_focus_wins_over_a_stale_active_state() {
    use pcbridge_native::accessibility::FocusHint;
    // In `editor` the editor's window is ACTIVE; the hint names the other
    // application's process, as xfwm4 kept an ACTIVE window on Xfce 4.20
    // while the window manager had given the focus to Mousepad.
    let desktop = tree("editor");
    let plain = accessibility::focused(&desktop).expect("focused");
    assert_eq!(plain["app"], "pcbridge-editor");
    let hint = FocusHint {
        pid: 4591,
        ..FocusHint::default()
    };
    let hinted = accessibility::focused_hinted(&desktop, Some(&hint)).expect("hinted");
    assert_eq!(hinted["app"], "gnome-shell");
    let mut dump_request = request(&serde_json::json!({}));
    dump_request.focus = Some(hint);
    let dump = accessibility::dump(&desktop, &dump_request).expect("dump");
    assert_eq!(dump["app"], "gnome-shell");
    // A process no application belongs to falls back to the ACTIVE state.
    let unknown = FocusHint {
        pid: 9999,
        ..FocusHint::default()
    };
    let fallback = accessibility::focused_hinted(&desktop, Some(&unknown)).expect("fallback");
    assert_eq!(fallback["app"], "pcbridge-editor");
}

fn named_hint(title: &str, apps: &[&str]) -> pcbridge_native::accessibility::FocusHint {
    pcbridge_native::accessibility::FocusHint {
        pid: 0,
        title: title.to_owned(),
        apps: apps.iter().map(|name| (*name).to_owned()).collect(),
    }
}

#[test]
fn the_compositors_window_title_wins_over_a_stale_active_state() {
    // GNOME's extension names the focused window by title and application,
    // with no process. In `editor` the document window is ACTIVE; the
    // compositor says the preferences window has the focus.
    let desktop = tree("editor");
    let hint = named_hint("Tercihler", &["pcbridge-editor", "org.pcbridge.Editor"]);
    let focused = accessibility::focused_hinted(&desktop, Some(&hint)).expect("hinted");
    assert_eq!(focused["window"], "Tercihler");
    let mut dump_request = request(&serde_json::json!({}));
    dump_request.focus = Some(hint.clone());
    let dump = accessibility::dump(&desktop, &dump_request).expect("dump");
    assert_eq!(dump["window"], "Tercihler");
    assert_eq!(dump["scope"], "window");
    // The window list marks that window, and only that one.
    let windows = accessibility::windows_hinted(&desktop, Some(&hint)).expect("windows");
    let active: Vec<&Value> = windows["windows"]
        .as_array()
        .expect("windows")
        .iter()
        .filter(|window| window["active"] == true)
        .map(|window| &window["window"])
        .collect();
    assert_eq!(active, vec!["Tercihler"]);
    // Without a hint the list keeps AT-SPI's ACTIVE state.
    let plain = accessibility::windows(&desktop).expect("windows");
    let active: Vec<&Value> = plain["windows"]
        .as_array()
        .expect("windows")
        .iter()
        .filter(|window| window["active"] == true)
        .map(|window| &window["window"])
        .collect();
    assert_eq!(active, vec!["Belge — Düzenleyici"]);
}

#[test]
fn a_window_no_window_marks_active_is_found_by_the_compositors_hint() {
    // Chromium and the GNOME file chooser leave every window without ACTIVE
    // (measured 2026-11-15). `similar_names`: the terminal is ACTIVE, the
    // text editor is not.
    let desktop = tree("similar_names");
    let by_title = named_hint("gnome-text-editor penceresi", &["org.gnome.TextEditor"]);
    let found = accessibility::focused_hinted(&desktop, Some(&by_title)).expect("by title");
    assert_eq!(found["app"], "gnome-text-editor");
    // A title AT-SPI does not know: the application's name alone, when one
    // application of that name has one window.
    let by_name = named_hint("Some other title", &["gnome-text-editor.desktop"]);
    let found = accessibility::focused_hinted(&desktop, Some(&by_name)).expect("by name");
    assert_eq!(found["app"], "gnome-text-editor");
    // A hint that matches nothing falls back to the ACTIVE state.
    let unknown = named_hint("A game", &["sdl-game"]);
    let found = accessibility::focused_hinted(&desktop, Some(&unknown)).expect("fallback");
    assert_eq!(found["app"], "gnome-terminal-server");
}

#[test]
fn two_windows_of_one_title_are_told_apart_by_the_active_state() {
    let desktop = tree("two_pythons");
    let hint = named_hint("python3 penceresi", &["python3"]);
    let found = accessibility::focused_hinted(&desktop, Some(&hint)).expect("hinted");
    assert_eq!(found["app_pid"], 8001);
}

#[test]
fn no_focus_names_the_compositors_window_and_what_to_pass_instead() {
    let desktop = tree("nothing_active");
    let plain = accessibility::focused(&desktop).expect_err("nothing is active");
    assert_eq!(plain.code, "TARGET_MISMATCH");
    assert!(plain.message.contains("window_list"), "{}", plain.message);
    let hint = named_hint("Download - Helium", &["helium"]);
    let mut dump_request = request(&serde_json::json!({}));
    dump_request.focus = Some(hint);
    let error = accessibility::dump(&desktop, &dump_request).expect_err("no match");
    assert_eq!(error.code, "TARGET_MISMATCH");
    assert!(
        error.message.contains("'Download - Helium'"),
        "{}",
        error.message
    );
    assert!(
        error.message.contains("application's name as target"),
        "{}",
        error.message
    );
}

#[test]
fn editable_means_what_set_text_needs() {
    // A Qt Quick text field carries the EDITABLE state with Text but no
    // EditableText (Free Download Manager, measured 2026-11-15). It is still
    // listed, as a field that takes typing, but not as editable.
    let fixture = serde_json::json!({
        "apps": {"qt": {"name": "qt-app", "bus": ":1.77", "pid": 7700, "windows": [{
            "ref": "/qt/w", "role": "frame", "name": "Qt", "states": ["active", "showing"],
            "children": [
                {"ref": "/qt/state-only", "role": "text", "name": "", "text": "",
                 "states": ["showing", "enabled", "sensitive", "editable", "focused"]},
                {"ref": "/qt/writable", "role": "text", "name": "Name", "text": "",
                 "editable_text": true,
                 "states": ["showing", "enabled", "sensitive", "editable"]}
            ]
        }]}},
        "desktops": {"qt": ["qt"]}
    });
    let desktop = FixtureTree::from_fixture(&fixture, "qt").expect("fixture loads");
    let dump = accessibility::dump(&desktop, &request(&serde_json::json!({}))).expect("dump");
    let nodes = dump["nodes"].as_array().expect("nodes");
    let by_ref = |path: &str| {
        nodes
            .iter()
            .find(|node| node["ref"] == path)
            .unwrap_or_else(|| panic!("{path} listed"))
    };
    let state_only = by_ref("/qt/state-only");
    assert_eq!(state_only["editable"], false);
    assert!(
        state_only["states"]
            .as_array()
            .expect("states")
            .contains(&Value::from("editable"))
    );
    assert_eq!(by_ref("/qt/writable")["editable"], true);
}
