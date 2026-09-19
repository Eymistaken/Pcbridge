//! Accessibility reads against the shared fixture (Task 6.2).
//!
//! `tests/fixtures/native/accessibility_cases.json` is the file the Python
//! helper's contract test reads too: the same desktops, the same expected
//! dumps. A case passes only when the native walk lists exactly what the
//! helper lists -- node for node, field for field.

use std::time::{Duration, Instant};

use pcbridge_native::platform::linux::accessibility::fixture::FixtureTree;
use pcbridge_native::platform::linux::accessibility::{
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
    assert!(error.message.contains("15 saniyede"), "{}", error.message);
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
