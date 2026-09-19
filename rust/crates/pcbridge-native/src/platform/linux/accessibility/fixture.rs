//! A fake desktop read from `tests/fixtures/native/accessibility_cases.json`.
//!
//! The Python helper's contract test builds the same desktops for libatspi's
//! place. Here they stand in for the accessibility bus, so both readers are
//! held to one set of trees and one set of expected dumps. A fixture only
//! describes; it never touches a real application.
//!
//! Actions change the fake the way the Python contract's fake changes: an
//! action that takes effect is recorded in [`FixtureTree::performed`], and a
//! text write replaces the node's text. Node keys beyond the tree's shape:
//! `"text"` gives the Text interface, `"editable_text"` the EditableText
//! one, `"max_chars"` cuts a written text short, `"read_only"` answers
//! writes with false, and `"refuses_actions"` answers `DoAction` with false.

use std::collections::{HashMap, HashSet};
use std::sync::Mutex;

use serde_json::Value;

use super::{CallError, NodeInfo, ObjectRef, States, Tree, state_bit};

/// The path libatspi gives every application root.
pub const APPLICATION_ROOT: &str = "/org/a11y/atspi/accessible/root";

const ACCESSIBLE: &str = "org.a11y.atspi.Accessible";
const ACTION: &str = "org.a11y.atspi.Action";
const TEXT: &str = "org.a11y.atspi.Text";
const EDITABLE_TEXT: &str = "org.a11y.atspi.EditableText";

#[derive(Debug, Default)]
pub struct FixtureTree {
    applications: Vec<ObjectRef>,
    nodes: HashMap<ObjectRef, NodeInfo>,
    pids: HashMap<String, u32>,
    interfaces: HashMap<ObjectRef, Vec<String>>,
    max_chars: HashMap<ObjectRef, usize>,
    read_only: HashSet<ObjectRef>,
    refuses_actions: HashSet<ObjectRef>,
    texts: Mutex<HashMap<ObjectRef, String>>,
    performed: Mutex<Vec<(String, String)>>,
}

impl FixtureTree {
    /// The desktop `desktop` of a parsed fixture.
    pub fn from_fixture(fixture: &Value, desktop: &str) -> Result<Self, String> {
        let keys = fixture["desktops"][desktop]
            .as_array()
            .ok_or_else(|| format!("fixture has no desktop {desktop:?}"))?;
        let mut tree = Self::default();
        for key in keys {
            let key = key.as_str().ok_or("desktop entries are app keys")?;
            let app = &fixture["apps"][key];
            let bus = text(app, "bus")?;
            let name = text(app, "name")?;
            let pid = app["pid"]
                .as_u64()
                .and_then(|pid| u32::try_from(pid).ok())
                .ok_or_else(|| format!("app {key:?} has no pid"))?;
            let root = ObjectRef::new(&bus, APPLICATION_ROOT);
            let windows = app["windows"]
                .as_array()
                .ok_or_else(|| format!("app {key:?} has no windows"))?;
            let mut children = Vec::new();
            for window in windows {
                children.push(Some(tree.insert(&bus, window)?));
            }
            tree.nodes.insert(
                root.clone(),
                NodeInfo {
                    role: "application".to_owned(),
                    name,
                    children,
                    ..NodeInfo::default()
                },
            );
            tree.pids.insert(bus, pid);
            tree.applications.push(root);
        }
        Ok(tree)
    }

    fn insert(&mut self, bus: &str, node: &Value) -> Result<ObjectRef, String> {
        let reference = ObjectRef::new(bus, text(node, "ref")?);
        let mut states = States::default();
        for state in strings(node, "states")? {
            let bit = state_bit(&state).ok_or_else(|| format!("unknown state {state:?}"))?;
            states.insert(bit);
        }
        let mut children = Vec::new();
        if let Some(list) = node["children"].as_array() {
            for child in list {
                children.push(Some(self.insert(bus, child)?));
            }
        }
        let info = NodeInfo {
            role: text(node, "role")?,
            name: node["name"].as_str().unwrap_or_default().trim().to_owned(),
            states,
            actions: strings(node, "actions")?,
            children,
        };
        let mut interfaces = vec![ACCESSIBLE.to_owned()];
        if !info.actions.is_empty() {
            interfaces.push(ACTION.to_owned());
        }
        if let Some(initial) = node["text"].as_str() {
            interfaces.push(TEXT.to_owned());
            self.texts
                .get_mut()
                .map_err(|_| "text store poisoned")?
                .insert(reference.clone(), initial.to_owned());
        }
        if node["editable_text"].as_bool() == Some(true) {
            interfaces.push(EDITABLE_TEXT.to_owned());
        }
        if let Some(limit) = node["max_chars"].as_u64() {
            let limit = usize::try_from(limit).map_err(|_| "max_chars is too large")?;
            self.max_chars.insert(reference.clone(), limit);
        }
        if node["read_only"].as_bool() == Some(true) {
            self.read_only.insert(reference.clone());
        }
        if node["refuses_actions"].as_bool() == Some(true) {
            self.refuses_actions.insert(reference.clone());
        }
        self.interfaces.insert(reference.clone(), interfaces);
        if self.nodes.insert(reference.clone(), info).is_some() {
            return Err(format!("object path {} appears twice", reference.path));
        }
        Ok(reference)
    }

    /// Every action that took effect, in order: `(object path, action)`,
    /// with "settext" for a text write.
    #[must_use]
    pub fn performed(&self) -> Vec<(String, String)> {
        self.performed
            .lock()
            .map(|performed| performed.clone())
            .unwrap_or_default()
    }

    /// Every text node's text now, by object path.
    #[must_use]
    pub fn texts(&self) -> HashMap<String, String> {
        self.texts
            .lock()
            .map(|texts| {
                texts
                    .iter()
                    .map(|(node, text)| (node.path.clone(), text.clone()))
                    .collect()
            })
            .unwrap_or_default()
    }

    /// The text of the node at `path` now.
    #[must_use]
    pub fn text_of(&self, path: &str) -> Option<String> {
        let texts = self.texts.lock().ok()?;
        texts
            .iter()
            .find(|(node, _)| node.path == path)
            .map(|(_, text)| text.clone())
    }

    fn record(&self, node: &ObjectRef, action: &str) {
        if let Ok(mut performed) = self.performed.lock() {
            let entry = (node.path.clone(), action.to_owned());
            // A write is one action however many calls it takes, as in the
            // Python contract's fake.
            if action != "settext" || !performed.contains(&entry) {
                performed.push(entry);
            }
        }
    }

    fn unknown(node: &ObjectRef) -> CallError {
        CallError::Refused(format!(
            "org.freedesktop.DBus.Error.UnknownObject: {}",
            node.path
        ))
    }
}

fn text(value: &Value, key: &str) -> Result<String, String> {
    value[key]
        .as_str()
        .map(str::to_owned)
        .ok_or_else(|| format!("missing string {key:?}"))
}

fn strings(value: &Value, key: &str) -> Result<Vec<String>, String> {
    match &value[key] {
        Value::Null => Ok(Vec::new()),
        Value::Array(items) => items
            .iter()
            .map(|item| {
                item.as_str()
                    .map(str::to_owned)
                    .ok_or_else(|| format!("{key:?} holds a non-string"))
            })
            .collect(),
        _ => Err(format!("{key:?} is not a list")),
    }
}

impl Tree for FixtureTree {
    fn applications(&self) -> Result<Vec<ObjectRef>, super::AccessibilityError> {
        Ok(self.applications.clone())
    }

    fn read(&self, nodes: &[ObjectRef]) -> Vec<Option<NodeInfo>> {
        nodes
            .iter()
            .map(|node| self.nodes.get(node).cloned())
            .collect()
    }

    fn pid(&self, bus: &str) -> u32 {
        self.pids.get(bus).copied().unwrap_or(0)
    }

    fn interfaces(&self, node: &ObjectRef) -> Result<Vec<String>, CallError> {
        self.interfaces
            .get(node)
            .cloned()
            .ok_or_else(|| Self::unknown(node))
    }

    fn do_action(&self, node: &ObjectRef, index: i32) -> Result<bool, CallError> {
        let info = self.nodes.get(node).ok_or_else(|| Self::unknown(node))?;
        let action = usize::try_from(index)
            .ok()
            .and_then(|index| info.actions.get(index));
        match action {
            Some(action) if !self.refuses_actions.contains(node) => {
                self.record(node, action);
                Ok(true)
            }
            // GTK4's answer for an insensitive widget or an unknown index.
            _ => Ok(false),
        }
    }

    fn set_text_contents(&self, node: &ObjectRef, text: &str) -> Result<bool, CallError> {
        let writable = self
            .interfaces
            .get(node)
            .is_some_and(|interfaces| interfaces.iter().any(|name| name == EDITABLE_TEXT));
        if !writable {
            return Err(CallError::Refused(
                "org.freedesktop.DBus.Error.UnknownMethod".to_owned(),
            ));
        }
        if self.read_only.contains(node) {
            return Ok(false);
        }
        let kept: String = match self.max_chars.get(node) {
            Some(limit) => text.chars().take(*limit).collect(),
            None => text.to_owned(),
        };
        self.texts
            .lock()
            .map_err(|_| CallError::Lost("text store poisoned".to_owned()))?
            .insert(node.clone(), kept);
        self.record(node, "settext");
        Ok(true)
    }

    fn character_count(&self, node: &ObjectRef) -> Result<i32, CallError> {
        let text = self.text(node)?;
        Ok(i32::try_from(text.chars().count()).unwrap_or(i32::MAX))
    }

    fn text(&self, node: &ObjectRef) -> Result<String, CallError> {
        self.texts
            .lock()
            .map_err(|_| CallError::Lost("text store poisoned".to_owned()))?
            .get(node)
            .cloned()
            .ok_or_else(|| Self::unknown(node))
    }
}
