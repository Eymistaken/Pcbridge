//! A fake desktop read from `tests/fixtures/native/accessibility_cases.json`.
//!
//! The Python helper's contract test builds the same desktops for libatspi's
//! place. Here they stand in for the accessibility bus, so both readers are
//! held to one set of trees and one set of expected dumps. A fixture only
//! describes; it never touches a real application.

use std::collections::HashMap;

use serde_json::Value;

use super::{NodeInfo, ObjectRef, States, Tree, state_bit};

/// The path libatspi gives every application root.
pub const APPLICATION_ROOT: &str = "/org/a11y/atspi/accessible/root";

#[derive(Debug, Default)]
pub struct FixtureTree {
    applications: Vec<ObjectRef>,
    nodes: HashMap<ObjectRef, NodeInfo>,
    pids: HashMap<String, u32>,
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
        if self.nodes.insert(reference.clone(), info).is_some() {
            return Err(format!("object path {} appears twice", reference.path));
        }
        Ok(reference)
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
}
