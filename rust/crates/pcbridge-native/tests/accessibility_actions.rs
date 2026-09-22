//! Accessibility actions against the shared fixture (Task 6.3).
//!
//! Every `action_cases` entry of `tests/fixtures/native/accessibility_cases.json`
//! runs here the way the Python helper's contract runs it: dump one desktop,
//! switch to the desktop the application changed into, act on the dumped
//! node. What took effect and what the fields hold afterwards must match the
//! expectation, and a refusal must have sent nothing.

use std::cell::{Cell, RefCell};
use std::time::{Duration, Instant};

use pcbridge_native::platform::linux::accessibility::action::{
    self, SEARCH_LIMIT, TEXT_SETTLE, Target,
};
use pcbridge_native::platform::linux::accessibility::fixture::FixtureTree;
use pcbridge_native::platform::linux::accessibility::{
    self, AccessibilityError, CallError, DumpRecord, DumpRequest, NodeInfo, ObjectRef, Tree, quoted,
};
use serde_json::Value;

const FIXTURE: &str = include_str!("../../../../tests/fixtures/native/accessibility_cases.json");

fn fixture() -> Value {
    serde_json::from_str(FIXTURE).expect("fixture is JSON")
}

fn tree_from(fixture: &Value, desktop: &str) -> FixtureTree {
    FixtureTree::from_fixture(fixture, desktop).expect("fixture desktop loads")
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

fn target<'a>(record: &'a DumpRecord, reference: &str) -> Target<'a> {
    Target {
        record,
        node: record
            .nodes
            .get(reference)
            .unwrap_or_else(|| panic!("the dump lists {reference}")),
        deadline: None,
        deadline_seconds: 8,
    }
}

/// The action a case describes, on `tree`: the helper's `act`/`settext`.
fn run(tree: &dyn Tree, target: &Target<'_>, act: &Value) -> Result<Value, AccessibilityError> {
    match act["cmd"].as_str() {
        Some("act") => {
            let prepared =
                action::prepare_act(tree, target, act["action"].as_str().unwrap_or("click"))?;
            action::perform_act(tree, prepared)
        }
        Some("settext") => {
            let prepared = action::prepare_set_text(tree, target)?;
            let text = act["text"].as_str().expect("settext carries text");
            action::perform_set_text(tree, prepared, text, TEXT_SETTLE)
        }
        other => panic!("unknown command {other:?}"),
    }
}

fn performed(tree: &FixtureTree) -> Value {
    Value::Array(
        tree.performed()
            .into_iter()
            .map(|(path, action)| serde_json::json!([path, action]))
            .collect(),
    )
}

#[test]
fn action_cases_match_the_shared_fixture() {
    let fixture = fixture();
    let cases = fixture["action_cases"].as_array().expect("action cases");
    let mut ran = 0;
    for case in cases {
        let name = case["name"].as_str().expect("case name");
        // A hand-made request with a blanked identity has no native form:
        // the helper takes identity only from its own dump record.
        if case["act"].get("override").is_some() {
            continue;
        }
        let dumped = tree_from(&fixture, case["dump"]["desktop"].as_str().expect("desktop"));
        let (_, record) = accessibility::read_dump(&dumped, &request(&case["dump"]["request"]))
            .unwrap_or_else(|error| panic!("{name}: dump failed: {error}"));
        let then = tree_from(&fixture, case["then"].as_str().expect("then"));
        let act = &case["act"];
        let result = run(
            &then,
            &target(&record, act["ref"].as_str().expect("ref")),
            act,
        );
        let expect = &case["expect"];
        assert_eq!(performed(&then), expect["performed"], "{name}: performed");
        if let Some(texts) = expect["text_after"].as_object() {
            for (path, text) in texts {
                assert_eq!(
                    then.text_of(path).as_deref(),
                    text.as_str(),
                    "{name}: text of {path}"
                );
            }
        }
        if expect["ok"] == false {
            let error = result.expect_err(name);
            assert_eq!(error.code, expect["code"], "{name}: {}", error.message);
        } else {
            let got = result.unwrap_or_else(|error| panic!("{name}: {error}"));
            for key in ["resolved_by", "replaced_chars", "now_chars", "verified"] {
                if let Some(want) = expect.get(key) {
                    assert_eq!(&got[key], want, "{name}: {key}");
                }
            }
            assert_eq!(got["ref"], act["ref"], "{name}: ref");
        }
        ran += 1;
    }
    assert!(ran >= 20, "only {ran} cases ran");
}

const BUS: &str = ":1.30";
const GROUP_A: &str = "/org/pcbridge/Editor/a11y/group-a";
const CLOSE_B: &str = "/org/pcbridge/Editor/a11y/close-b";

/// The editor with one more child under group A: `twin`, which reads as
/// the "Kapat" button of group B.
struct Twin {
    inner: FixtureTree,
    twin: ObjectRef,
}

impl Twin {
    fn new(bus: &str) -> Self {
        Self::on("editor", bus)
    }

    fn on(desktop: &str, bus: &str) -> Self {
        Self {
            inner: tree_from(&fixture(), desktop),
            twin: ObjectRef::new(bus, CLOSE_B),
        }
    }
}

impl Tree for Twin {
    fn applications(&self) -> Result<Vec<ObjectRef>, AccessibilityError> {
        self.inner.applications()
    }

    fn read(&self, nodes: &[ObjectRef]) -> Vec<Option<NodeInfo>> {
        nodes
            .iter()
            .map(|node| {
                let asked = if *node == self.twin {
                    ObjectRef::new(BUS, CLOSE_B)
                } else {
                    node.clone()
                };
                let mut info = self
                    .inner
                    .read(std::slice::from_ref(&asked))
                    .pop()
                    .flatten()?;
                if *node == ObjectRef::new(BUS, GROUP_A) {
                    info.children.push(Some(self.twin.clone()));
                }
                Some(info)
            })
            .collect()
    }

    fn pid(&self, bus: &str) -> u32 {
        self.inner.pid(bus)
    }

    fn do_action(&self, node: &ObjectRef, index: i32) -> Result<bool, CallError> {
        self.inner.do_action(node, index)
    }
}

#[test]
fn the_same_object_met_twice_is_one_target() {
    // On D-Bus a bus name and a path are one object: listed twice, it is
    // still the one button.
    let tree = Twin::new(BUS);
    let (dump, record) = accessibility::read_dump(&tree, &request(&Value::Null)).expect("dump");
    let listed = dump["nodes"]
        .as_array()
        .expect("nodes")
        .iter()
        .filter(|node| node["ref"] == CLOSE_B)
        .count();
    assert_eq!(listed, 2, "the dump lists it where it met it");
    assert!(!record.nodes[CLOSE_B].shared);
    let got = run(
        &tree,
        &target(&record, CLOSE_B),
        &serde_json::json!({"cmd": "act"}),
    )
    .expect("acted");
    assert_eq!(got["resolved_by"], "path");
    assert_eq!(
        tree.inner.performed(),
        vec![(CLOSE_B.to_owned(), "click".to_owned())]
    );
}

#[test]
fn a_moved_element_is_found_by_bus_and_path_not_path_alone() {
    // After a button is prepended the index path leads elsewhere and the
    // search runs. It meets a foreign object with the path of "Kapat" (B)
    // first, under group A, and must pass it by.
    let (_, record) =
        accessibility::read_dump(&tree_from(&fixture(), "editor"), &request(&Value::Null))
            .expect("dump");
    let then = Twin::on("editor_prepend", ":1.99");
    let got = run(
        &then,
        &target(&record, CLOSE_B),
        &serde_json::json!({"cmd": "act"}),
    )
    .expect("the real button");
    assert_eq!(got["resolved_by"], "moved");
    assert_eq!(
        then.inner.performed(),
        vec![(CLOSE_B.to_owned(), "click".to_owned())]
    );
}

#[test]
fn one_path_on_two_buses_is_never_guessed() {
    // An embedded object on another bus with the path of a node of the
    // application: the path the host sends names neither alone.
    let tree = Twin::new(":1.99");
    let (_, record) = accessibility::read_dump(&tree, &request(&Value::Null)).expect("dump");
    assert!(record.nodes[CLOSE_B].shared);
    let error = run(
        &tree,
        &target(&record, CLOSE_B),
        &serde_json::json!({"cmd": "act"}),
    )
    .expect_err("refused");
    assert_eq!(error.code, "ELEMENT_AMBIGUOUS");
    assert!(tree.inner.performed().is_empty());
}

#[test]
fn messages_read_as_the_python_helpers() {
    assert_eq!(quoted("Kapat"), "'Kapat'");
    assert_eq!(quoted("Belge — Düzenleyici"), "'Belge — Düzenleyici'");
    assert_eq!(quoted("it's"), "\"it's\"");
    assert_eq!(quoted("a'b\"c"), "'a\\'b\"c'");
    assert_eq!(quoted("tab\there"), "'tab\\there'");
    assert_eq!(quoted("back\\slash"), "'back\\\\slash'");
    assert_eq!(quoted("bell\u{7}"), "'bell\\x07'");
}

/// A tree whose calls that act all time out, and that counts them.
struct Frozen {
    inner: FixtureTree,
    actions: Cell<usize>,
    writes: Cell<usize>,
}

impl Tree for Frozen {
    fn applications(&self) -> Result<Vec<ObjectRef>, AccessibilityError> {
        self.inner.applications()
    }

    fn read(&self, nodes: &[ObjectRef]) -> Vec<Option<NodeInfo>> {
        self.inner.read(nodes)
    }

    fn pid(&self, bus: &str) -> u32 {
        self.inner.pid(bus)
    }

    fn interfaces(&self, node: &ObjectRef) -> Result<Vec<String>, CallError> {
        self.inner.interfaces(node)
    }

    fn do_action(&self, _node: &ObjectRef, _index: i32) -> Result<bool, CallError> {
        self.actions.set(self.actions.get() + 1);
        Err(CallError::Timeout)
    }

    fn set_text_contents(&self, _node: &ObjectRef, _text: &str) -> Result<bool, CallError> {
        self.writes.set(self.writes.get() + 1);
        Err(CallError::Lost(
            "org.freedesktop.DBus.Error.NoReply".to_owned(),
        ))
    }

    fn character_count(&self, node: &ObjectRef) -> Result<i32, CallError> {
        self.inner.character_count(node)
    }
}

#[test]
fn an_unanswered_action_is_unknown_and_sent_once() {
    let fixture = fixture();
    let (_, record) =
        accessibility::read_dump(&tree_from(&fixture, "editor"), &request(&Value::Null))
            .expect("dump");
    let frozen = Frozen {
        inner: tree_from(&fixture, "editor"),
        actions: Cell::new(0),
        writes: Cell::new(0),
    };
    let error = run(
        &frozen,
        &target(&record, "/org/pcbridge/Editor/a11y/ok"),
        &serde_json::json!({"cmd": "act"}),
    )
    .expect_err("no answer");
    assert_eq!(error.code, "EXECUTION_UNKNOWN");
    assert!(error.message.contains("Not repeated"), "{}", error.message);
    assert_eq!(frozen.actions.get(), 1);

    let error = run(
        &frozen,
        &target(&record, "/org/pcbridge/Editor/a11y/field-name"),
        &serde_json::json!({"cmd": "settext", "text": "x"}),
    )
    .expect_err("left without answering");
    assert_eq!(error.code, "EXECUTION_UNKNOWN");
    assert_eq!(frozen.writes.get(), 1);
}

#[test]
fn a_passed_deadline_sends_nothing() {
    let fixture = fixture();
    let (_, record) =
        accessibility::read_dump(&tree_from(&fixture, "editor"), &request(&Value::Null))
            .expect("dump");
    let then = tree_from(&fixture, "editor_prepend");
    let late = Target {
        deadline: Some(
            Instant::now()
                .checked_sub(Duration::from_secs(1))
                .expect("past"),
        ),
        ..target(&record, "/org/pcbridge/Editor/a11y/close-b")
    };
    let error = run(&then, &late, &serde_json::json!({"cmd": "act"})).expect_err("too late");
    assert_eq!(error.code, "TIMEOUT");
    assert!(then.performed().is_empty());
}

/// A tree that applies a written text only on the third read back, the way
/// a toolkit that applies it later would.
struct Late {
    inner: FixtureTree,
    reads: Cell<usize>,
    written: RefCell<Option<String>>,
}

impl Tree for Late {
    fn applications(&self) -> Result<Vec<ObjectRef>, AccessibilityError> {
        self.inner.applications()
    }

    fn read(&self, nodes: &[ObjectRef]) -> Vec<Option<NodeInfo>> {
        self.inner.read(nodes)
    }

    fn pid(&self, bus: &str) -> u32 {
        self.inner.pid(bus)
    }

    fn interfaces(&self, node: &ObjectRef) -> Result<Vec<String>, CallError> {
        self.inner.interfaces(node)
    }

    fn set_text_contents(&self, _node: &ObjectRef, text: &str) -> Result<bool, CallError> {
        *self.written.borrow_mut() = Some(text.to_owned());
        Ok(true)
    }

    fn character_count(&self, node: &ObjectRef) -> Result<i32, CallError> {
        self.inner.character_count(node)
    }

    fn text(&self, node: &ObjectRef) -> Result<String, CallError> {
        self.reads.set(self.reads.get() + 1);
        if self.reads.get() >= 3 {
            Ok(self.written.borrow().clone().unwrap_or_default())
        } else {
            self.inner.text(node)
        }
    }
}

#[test]
fn a_text_applied_a_little_later_is_still_verified() {
    let fixture = fixture();
    let (_, record) =
        accessibility::read_dump(&tree_from(&fixture, "editor"), &request(&Value::Null))
            .expect("dump");
    let late = Late {
        inner: tree_from(&fixture, "editor"),
        reads: Cell::new(0),
        written: RefCell::new(None),
    };
    let got = run(
        &late,
        &target(&record, "/org/pcbridge/Editor/a11y/field-name"),
        &serde_json::json!({"cmd": "settext", "text": "Çağrı"}),
    )
    .expect("written");
    assert_eq!(got["verified"], true);
    assert_eq!(got["now_chars"], 5);
    assert_eq!(late.reads.get(), 3);
}

#[test]
fn a_short_text_is_reported_with_counts_only() {
    let fixture = fixture();
    let tree = tree_from(&fixture, "form");
    let (_, record) = accessibility::read_dump(&tree, &request(&Value::Null)).expect("dump");
    let secret = "gizli-içerik-123";
    let started = Instant::now();
    let error = run(
        &tree,
        &target(&record, "/org/pcbridge/Form/a11y/code"),
        &serde_json::json!({"cmd": "settext", "text": secret}),
    )
    .expect_err("cut short");
    assert_eq!(error.code, "TEXT_MISMATCH");
    assert!(!error.message.contains("gizli"), "{}", error.message);
    assert!(!error.message.contains("gizli-"), "{}", error.message);
    assert!(error.message.contains("16 characters"), "{}", error.message);
    assert!(error.message.contains("now holds 5"), "{}", error.message);
    // It waited for the text to settle before calling it short.
    assert!(started.elapsed() >= TEXT_SETTLE, "{:?}", started.elapsed());
}

#[test]
fn the_search_limit_is_the_dumps_visit_cap() {
    assert_eq!(
        SEARCH_LIMIT,
        accessibility::DEFAULT_MAX_NODES * accessibility::VISIT_FACTOR
    );
}
