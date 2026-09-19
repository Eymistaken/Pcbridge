//! AT-SPI accessibility reads without GI (Task 6.2).
//!
//! The Python helper (`pcbridge/desktop/atspi_helper.py`) reads the tree
//! through libatspi. This module reads the same tree straight off the
//! accessibility bus and must list exactly what the helper lists: the same
//! nodes, in the same order, with the same filters. Both are held to
//! `tests/fixtures/native/accessibility_cases.json`.
//!
//! Two measured differences between the raw bus and libatspi decide how the
//! bus is read (2026-09-19, GTK4 4.14):
//!
//! - `GetRoleName` returns the toolkit's own words: GTK4 calls its window
//!   frame "application" and a push button "button". libatspi maps the
//!   numeric `GetRole` through its own table instead, and that table is what
//!   every filter and every stored role compares against. So the number is
//!   read and mapped here with the same table ([`ROLE_NAMES`]).
//! - `Action.GetActions` returns localized names ("Click"); libatspi's action
//!   name is `GetName(i)` ("click"). The canonical one is read.
//!
//! The walk is the helper's, step for step: depth-first, left to right, at
//! most `max_nodes * 25` visits and depth 100. Only fetching differs: the
//! children of a node are read concurrently, which changes the latency and
//! nothing else.
//!
//! Actions (Task 6.3) are in [`action`].

pub mod action;
pub mod bus;
pub mod fixture;

use std::collections::HashMap;
use std::time::Instant;

use serde_json::{Value, json};

pub const MAX_DEPTH: usize = 100;
pub const DEFAULT_MAX_NODES: usize = 400;
/// A walk visits at most this many nodes per node it may list.
pub const VISIT_FACTOR: usize = 25;

/// libatspi's `atspi_role_get_name`, index = `AtspiRole` (at-spi2-core 2.52):
/// the enum nick with dashes turned into spaces.
pub const ROLE_NAMES: [&str; 131] = [
    "invalid",
    "accelerator label",
    "alert",
    "animation",
    "arrow",
    "calendar",
    "canvas",
    "check box",
    "check menu item",
    "color chooser",
    "column header",
    "combo box",
    "date editor",
    "desktop icon",
    "desktop frame",
    "dial",
    "dialog",
    "directory pane",
    "drawing area",
    "file chooser",
    "filler",
    "focus traversable",
    "font chooser",
    "frame",
    "glass pane",
    "html container",
    "icon",
    "image",
    "internal frame",
    "label",
    "layered pane",
    "list",
    "list item",
    "menu",
    "menu bar",
    "menu item",
    "option pane",
    "page tab",
    "page tab list",
    "panel",
    "password text",
    "popup menu",
    "progress bar",
    "push button",
    "radio button",
    "radio menu item",
    "root pane",
    "row header",
    "scroll bar",
    "scroll pane",
    "separator",
    "slider",
    "spin button",
    "split pane",
    "status bar",
    "table",
    "table cell",
    "table column header",
    "table row header",
    "tearoff menu item",
    "terminal",
    "text",
    "toggle button",
    "tool bar",
    "tool tip",
    "tree",
    "tree table",
    "unknown",
    "viewport",
    "window",
    "extended",
    "header",
    "footer",
    "paragraph",
    "ruler",
    "application",
    "autocomplete",
    "editbar",
    "embedded",
    "entry",
    "chart",
    "caption",
    "document frame",
    "heading",
    "page",
    "section",
    "redundant object",
    "form",
    "link",
    "input method window",
    "table row",
    "tree item",
    "document spreadsheet",
    "document presentation",
    "document text",
    "document web",
    "document email",
    "comment",
    "list box",
    "grouping",
    "image map",
    "notification",
    "info bar",
    "level bar",
    "title bar",
    "block quote",
    "audio",
    "video",
    "definition",
    "article",
    "landmark",
    "log",
    "marquee",
    "math",
    "rating",
    "timer",
    "static",
    "math fraction",
    "math root",
    "subscript",
    "superscript",
    "description list",
    "description term",
    "description value",
    "footnote",
    "content deletion",
    "content insertion",
    "mark",
    "suggestion",
    "push button menu",
    "last defined",
];

/// `ATSPI_ROLE_EXTENDED`: the toolkit names the role itself.
const ROLE_EXTENDED: u32 = 70;
/// libatspi asks the toolkit (`GetRoleName`) only past its own table.
const ROLE_TABLE_END: u32 = 130;

/// libatspi's name for a numeric role, or `None` where libatspi asks the
/// toolkit for the name instead.
#[must_use]
pub fn role_name(role: u32) -> Option<&'static str> {
    if role < ROLE_TABLE_END && role != ROLE_EXTENDED {
        ROLE_NAMES.get(role as usize).copied()
    } else {
        None
    }
}

/// `AtspiStateType` bits, by their nick (the fixture's spelling).
pub const STATE_NAMES: [&str; 44] = [
    "invalid",
    "active",
    "armed",
    "busy",
    "checked",
    "collapsed",
    "defunct",
    "editable",
    "enabled",
    "expandable",
    "expanded",
    "focusable",
    "focused",
    "has-tooltip",
    "horizontal",
    "iconified",
    "modal",
    "multi-line",
    "multiselectable",
    "opaque",
    "pressed",
    "resizable",
    "selectable",
    "selected",
    "sensitive",
    "showing",
    "single-line",
    "stale",
    "transient",
    "vertical",
    "visible",
    "manages-descendants",
    "indeterminate",
    "required",
    "truncated",
    "animated",
    "invalid-entry",
    "supports-autocompletion",
    "selectable-text",
    "is-default",
    "visited",
    "checkable",
    "has-popup",
    "read-only",
];

pub const STATE_ACTIVE: u32 = 1;
pub const STATE_SHOWING: u32 = 25;

/// The states worth showing in a dump, in the helper's order and spelling.
const STATE_FLAGS: [(u32, &str); 7] = [
    (8, "enabled"),
    (24, "sensitive"),
    (12, "focused"),
    (4, "checked"),
    (23, "selected"),
    (7, "editable"),
    (10, "expanded"),
];

#[must_use]
pub fn state_bit(name: &str) -> Option<u32> {
    STATE_NAMES
        .iter()
        .position(|candidate| *candidate == name)
        .and_then(|bit| u32::try_from(bit).ok())
}

/// A set of `AtspiStateType` bits, as `GetState` sends them (two words).
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct States(pub u64);

impl States {
    #[must_use]
    pub fn from_words(words: &[u32]) -> Self {
        let low = u64::from(words.first().copied().unwrap_or(0));
        let high = u64::from(words.get(1).copied().unwrap_or(0));
        Self(low | (high << 32))
    }

    #[must_use]
    pub const fn contains(self, bit: u32) -> bool {
        bit < 64 && self.0 & (1 << bit) != 0
    }

    pub fn insert(&mut self, bit: u32) {
        if bit < 64 {
            self.0 |= 1 << bit;
        }
    }

    fn labels(self) -> Vec<&'static str> {
        STATE_FLAGS
            .iter()
            .filter(|(bit, _)| self.contains(*bit))
            .map(|(_, label)| *label)
            .collect()
    }
}

/// One accessible object: the owner's bus name and the object path.
#[derive(Clone, Debug, Eq, Hash, PartialEq)]
pub struct ObjectRef {
    pub bus: String,
    pub path: String,
}

impl ObjectRef {
    #[must_use]
    pub fn new(bus: impl Into<String>, path: impl Into<String>) -> Self {
        Self {
            bus: bus.into(),
            path: path.into(),
        }
    }
}

/// What a walk reads about one node. A field the toolkit refused to answer
/// is empty, the way libatspi's failures read in the helper.
#[derive(Clone, Debug, Default)]
pub struct NodeInfo {
    /// libatspi's role name; "?" when the role could not be read.
    pub role: String,
    pub name: String,
    pub states: States,
    /// Every action name, unfiltered: `is_real_action` is the walk's job.
    pub actions: Vec<String>,
    /// Children by position. A null reference keeps its index, because the
    /// index path of every later sibling depends on it.
    pub children: Vec<Option<ObjectRef>>,
}

/// Why a read failed. `code` is the shared taxonomy's; `message` is shown to
/// the model, so it is written the way the Python helper writes it.
#[derive(Clone, Debug, Eq, PartialEq, thiserror::Error)]
#[error("{message}")]
pub struct AccessibilityError {
    pub code: &'static str,
    pub message: String,
}

impl AccessibilityError {
    fn new(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }

    #[must_use]
    pub fn unavailable(message: impl Into<String>) -> Self {
        Self::new("BACKEND_UNAVAILABLE", message)
    }

    #[must_use]
    pub fn timeout(seconds: u64) -> Self {
        Self::new(
            "TIMEOUT",
            format!(
                "Uygulama {seconds} saniyede cevap vermedi. Donmus olabilir; ekran \
                 goruntusuyle bakin (screen_capture)."
            ),
        )
    }
}

/// How a call that changes something went wrong. Reads do not need this:
/// a failed read is an empty field, the way libatspi's failures read in the
/// helper. A call that acts must say whether it may have happened.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum CallError {
    /// No answer in time. The call was sent and may have been carried out.
    Timeout,
    /// The connection failed after sending, or the application left before
    /// answering. The call may have been carried out.
    Lost(String),
    /// The application answered with an error: the call was not carried out.
    Refused(String),
}

/// The source of a tree: the real bus or a fixture.
pub trait Tree {
    /// The applications registered on the desktop, in the registry's order.
    fn applications(&self) -> Result<Vec<ObjectRef>, AccessibilityError>;

    /// Read several nodes; one result per node, in order. Implementations
    /// may read them concurrently.
    fn read(&self, nodes: &[ObjectRef]) -> Vec<Option<NodeInfo>>;

    /// The process behind a bus name, 0 when unknown.
    fn pid(&self, bus: &str) -> u32;

    /// The AT-SPI interfaces `node` implements (`GetInterfaces`).
    fn interfaces(&self, _node: &ObjectRef) -> Result<Vec<String>, CallError> {
        Err(CallError::Refused(
            "interfaces are not readable here".to_owned(),
        ))
    }

    /// `Action.DoAction(index)`: the application's own answer.
    fn do_action(&self, _node: &ObjectRef, _index: i32) -> Result<bool, CallError> {
        Err(CallError::Refused(
            "actions are not available here".to_owned(),
        ))
    }

    /// `EditableText.SetTextContents(text)`: the application's own answer.
    fn set_text_contents(&self, _node: &ObjectRef, _text: &str) -> Result<bool, CallError> {
        Err(CallError::Refused("text is not writable here".to_owned()))
    }

    /// `Text.CharacterCount`.
    fn character_count(&self, _node: &ObjectRef) -> Result<i32, CallError> {
        Err(CallError::Refused("text is not readable here".to_owned()))
    }

    /// The whole text of a `Text` node: `CharacterCount`, then `GetText`
    /// up to that count. GTK4 answers `GetText(0, -1)` with an empty string
    /// (measured 2026-09-19), so the end is never left as -1.
    fn text(&self, _node: &ObjectRef) -> Result<String, CallError> {
        Err(CallError::Refused("text is not readable here".to_owned()))
    }
}

/// What a dump listed, kept by the helper that listed it (Task 6.3).
///
/// An action names a dump and one of its nodes; the helper acts only on a
/// node it listed itself, with the identity it read, never on an identity a
/// request brings along.
#[derive(Clone, Debug)]
pub struct DumpRecord {
    /// The application's name at dump time, "" when it had none.
    pub app: String,
    pub app_bus: String,
    /// The dumped window of a focused dump; `None` for a named dump, which
    /// covers the whole application.
    pub window: Option<ObjectRef>,
    /// The listed nodes by object path, the `ref` a dump returns.
    pub nodes: HashMap<String, NodeRecord>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct NodeRecord {
    /// The node itself: its owner's bus name and its object path.
    pub object: ObjectRef,
    /// Child indexes from the application root, as the dump found it.
    pub path: Vec<usize>,
    pub role: String,
    pub name: String,
    /// Another object in the same dump, on another bus, had the same object
    /// path, so the path the host sends names neither alone. An action on it
    /// is refused. (The same object listed twice is one object: it keeps its
    /// first listing.)
    pub shared: bool,
}

/// `repr()` of a Python string, for messages that must read as the Python
/// helper's do. Control characters are escaped; the names AT-SPI gives
/// carry nothing else Python would escape.
#[must_use]
pub fn quoted(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::with_capacity(text.len() + 2);
    out.push(quote);
    for character in text.chars() {
        match character {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            _ if character == quote => {
                out.push('\\');
                out.push(character);
            }
            _ if character.is_control() => {
                let code = u32::from(character);
                if code < 0x100 {
                    out.push_str(&format!("\\x{code:02x}"));
                } else {
                    out.push_str(&format!("\\u{code:04x}"));
                }
            }
            _ => out.push(character),
        }
    }
    out.push(quote);
    out
}

/// A dump request, mirroring the helper's `dump` command.
#[derive(Clone, Debug)]
pub struct DumpRequest {
    pub target: String,
    pub interactive_only: bool,
    pub max_nodes: usize,
    pub deadline: Option<Instant>,
    /// Seconds reported when the deadline passes.
    pub deadline_seconds: u64,
}

const TEXT_ROLES: [&str; 11] = [
    "label",
    "text",
    "heading",
    "static",
    "list item",
    "menu item",
    "table cell",
    "paragraph",
    "link",
    "tooltip",
    "status bar",
];
const INTERACTIVE_ROLES: [&str; 16] = [
    "push button",
    "toggle button",
    "check box",
    "radio button",
    "combo box",
    "entry",
    "text",
    "menu item",
    "link",
    "slider",
    "spin button",
    "tab",
    "list item",
    "menu",
    "check menu item",
    "radio menu item",
];
/// Containers are never targets, even when they report actions: Chromium
/// hangs `doDefault` on every node (measured 2026-08-02).
const CONTAINER_ROLES: [&str; 15] = [
    "application",
    "frame",
    "window",
    "dialog",
    "panel",
    "filler",
    "scroll pane",
    "viewport",
    "section",
    "document frame",
    "document web",
    "redundant object",
    "layered pane",
    "split pane",
    "tool bar",
];

/// `click` is an action; `page.save-as` is one of the GActions GTK4 hangs on
/// every node (measured 2026-08-02). The dot tells them apart.
#[must_use]
pub fn is_real_action(name: &str) -> bool {
    let name = name.trim();
    !name.is_empty() && !name.contains('.')
}

struct Kept {
    path: Vec<usize>,
    object: ObjectRef,
    role: String,
    name: String,
    states: Vec<&'static str>,
    actions: Vec<String>,
    editable: bool,
    depth: usize,
}

fn deadline_passed(request: &DumpRequest) -> Result<(), AccessibilityError> {
    match request.deadline {
        Some(deadline) if Instant::now() > deadline => {
            Err(AccessibilityError::timeout(request.deadline_seconds))
        }
        _ => Ok(()),
    }
}

/// Read one node, or a default (role "?") the way a failed libatspi read
/// looks in the helper.
fn read_one<T: Tree + ?Sized>(tree: &T, node: &ObjectRef) -> NodeInfo {
    tree.read(std::slice::from_ref(node))
        .into_iter()
        .next()
        .flatten()
        .unwrap_or_else(|| NodeInfo {
            role: "?".to_owned(),
            ..NodeInfo::default()
        })
}

/// The walk, node for node the helper's `_walk`.
fn walk<T: Tree + ?Sized>(
    tree: &T,
    root: &ObjectRef,
    root_info: NodeInfo,
    base: Vec<usize>,
    request: &DumpRequest,
) -> Result<(Vec<Kept>, bool), AccessibilityError> {
    let mut known: std::collections::HashMap<ObjectRef, NodeInfo> =
        std::collections::HashMap::new();
    known.insert(root.clone(), root_info);
    let mut out = Vec::new();
    let mut truncated = false;
    let mut stack = vec![(root.clone(), base, 0usize)];
    let mut visited = 0usize;
    let cap = request.max_nodes.saturating_mul(VISIT_FACTOR);

    while let Some((node, path, depth)) = stack.pop() {
        visited += 1;
        if visited > cap {
            truncated = true;
            break;
        }
        if depth > MAX_DEPTH {
            continue;
        }
        deadline_passed(request)?;
        let info = match known.remove(&node) {
            Some(info) => info,
            None => read_one(tree, &node),
        };

        let showing = info.states.contains(STATE_SHOWING);
        let states = info.states.labels();
        let actions: Vec<String> = info
            .actions
            .iter()
            .filter(|name| is_real_action(name))
            .map(|name| name.trim().to_owned())
            .collect();
        let editable = states.contains(&"editable");
        let container = CONTAINER_ROLES.contains(&info.role.as_str());
        let keep = showing
            && (editable
                || INTERACTIVE_ROLES.contains(&info.role.as_str())
                || (!actions.is_empty() && !container)
                || (!request.interactive_only
                    && !info.name.is_empty()
                    && TEXT_ROLES.contains(&info.role.as_str())));
        if keep && (!info.name.is_empty() || !actions.is_empty() || editable) {
            if out.len() >= request.max_nodes {
                truncated = true;
            } else {
                out.push(Kept {
                    path: path.clone(),
                    object: node.clone(),
                    role: info.role.clone(),
                    name: info.name.clone(),
                    states,
                    actions,
                    editable,
                    depth,
                });
            }
        }

        // Read the children together before descending: one round of
        // concurrent calls per node instead of one per child.
        let children: Vec<(usize, ObjectRef)> = info
            .children
            .iter()
            .enumerate()
            .filter_map(|(index, child)| child.clone().map(|child| (index, child)))
            .collect();
        let unread: Vec<ObjectRef> = children
            .iter()
            .map(|(_, child)| child.clone())
            .filter(|child| !known.contains_key(child))
            .collect();
        if !unread.is_empty() {
            for (child, read) in unread.iter().zip(tree.read(&unread)) {
                known.insert(
                    child.clone(),
                    read.unwrap_or_else(|| NodeInfo {
                        role: "?".to_owned(),
                        ..NodeInfo::default()
                    }),
                );
            }
        }
        for (index, child) in children.into_iter().rev() {
            let mut child_path = path.clone();
            child_path.push(index);
            stack.push((child, child_path, depth + 1));
        }
    }
    Ok((dedup(out), truncated))
}

/// Drop GTK4's wrapper nodes: an action-less named node is dropped when a
/// descendant with the same name has actions (`push button 'Ac'` around
/// `toggle button 'Ac'`). The helper's `_dedup`.
fn dedup(nodes: Vec<Kept>) -> Vec<Kept> {
    let actionable: Vec<(Vec<usize>, String)> = nodes
        .iter()
        .filter(|node| !node.actions.is_empty() || node.editable)
        .map(|node| (node.path.clone(), node.name.clone()))
        .collect();
    nodes
        .into_iter()
        .filter(|node| {
            if !node.actions.is_empty() || node.editable || node.name.is_empty() {
                return true;
            }
            !actionable.iter().any(|(path, name)| {
                path.len() > node.path.len()
                    && path[..node.path.len()] == node.path[..]
                    && *name == node.name
            })
        })
        .collect()
}

/// One application with its name and windows, read once.
struct App {
    reference: ObjectRef,
    info: NodeInfo,
    windows: Vec<(usize, ObjectRef, NodeInfo)>,
}

impl App {
    fn active(&self) -> Option<&(usize, ObjectRef, NodeInfo)> {
        self.windows
            .iter()
            .find(|(_, _, info)| info.states.contains(STATE_ACTIVE))
    }
}

fn read_apps<T: Tree + ?Sized>(tree: &T) -> Result<Vec<App>, AccessibilityError> {
    let references = tree.applications()?;
    let infos = tree.read(&references);
    let mut apps: Vec<App> = references
        .into_iter()
        .zip(infos)
        .filter_map(|(reference, info)| {
            info.map(|info| App {
                reference,
                info,
                windows: Vec::new(),
            })
        })
        .collect();
    let mut wanted: Vec<(usize, usize, ObjectRef)> = Vec::new();
    for (app_index, app) in apps.iter().enumerate() {
        for (window_index, window) in app.info.children.iter().enumerate() {
            if let Some(window) = window {
                wanted.push((app_index, window_index, window.clone()));
            }
        }
    }
    let windows: Vec<ObjectRef> = wanted.iter().map(|(.., window)| window.clone()).collect();
    for ((app_index, window_index, window), info) in wanted.into_iter().zip(tree.read(&windows)) {
        if let Some(info) = info {
            apps[app_index].windows.push((window_index, window, info));
        }
    }
    Ok(apps)
}

/// A dump as the helper's `dump` command returns it.
pub fn dump<T: Tree + ?Sized>(
    tree: &T,
    request: &DumpRequest,
) -> Result<Value, AccessibilityError> {
    read_dump(tree, request).map(|(dump, _record)| dump)
}

/// A dump, and the record an action on one of its nodes is checked against.
pub fn read_dump<T: Tree + ?Sized>(
    tree: &T,
    request: &DumpRequest,
) -> Result<(Value, DumpRecord), AccessibilityError> {
    let target = request.target.trim();
    let apps = read_apps(tree)?;
    deadline_passed(request)?;

    let (app, root, root_info, base, scope, window_ref, window_name, same_name) =
        if ["focused", "odak", "aktif"].contains(&target.to_lowercase().as_str()) {
            let found = apps
                .iter()
                .find_map(|app| app.active().map(|window| (app, window)));
            let Some((app, (index, window, info))) = found else {
                return Err(AccessibilityError::new(
                    "TARGET_MISMATCH",
                    "Odakta pencere yok (AT-SPI hicbir pencereyi ACTIVE isaretlemiyor). \
                     Bir pencereye tiklayin ya da target ile uygulama adi verin.",
                ));
            };
            (
                app,
                window.clone(),
                info.clone(),
                vec![*index],
                "window",
                window.path.clone(),
                info.name.clone(),
                1,
            )
        } else {
            let (app, same_name) = find_app(&apps, target)?;
            // The helper names an application dump after its first window.
            let window_name = app
                .windows
                .first()
                .map(|(_, _, info)| info.name.clone())
                .unwrap_or_default();
            (
                app,
                app.reference.clone(),
                app.info.clone(),
                Vec::new(),
                "app",
                String::new(),
                window_name,
                same_name,
            )
        };

    let (nodes, truncated) = walk(tree, &root, root_info, base, request)?;
    let mut record = DumpRecord {
        app: app.info.name.clone(),
        app_bus: app.reference.bus.clone(),
        window: (scope == "window").then(|| root.clone()),
        nodes: HashMap::with_capacity(nodes.len()),
    };
    for node in &nodes {
        let entry = NodeRecord {
            object: node.object.clone(),
            path: node.path.clone(),
            role: node.role.clone(),
            name: node.name.clone(),
            shared: false,
        };
        record
            .nodes
            .entry(node.object.path.clone())
            .and_modify(|kept| kept.shared |= kept.object != node.object)
            .or_insert(entry);
    }
    let dump = json!({
        "ok": true,
        "app": app.info.name,
        "app_bus": app.reference.bus,
        "app_pid": tree.pid(&app.reference.bus),
        "same_name": same_name,
        "scope": scope,
        "window": window_name,
        "window_ref": window_ref,
        "nodes": nodes.into_iter().map(|node| json!({
            "path": node.path,
            "ref": node.object.path,
            "role": node.role,
            "name": node.name,
            "states": node.states,
            "actions": node.actions,
            "editable": node.editable,
            "depth": node.depth,
        })).collect::<Vec<_>>(),
        "truncated": truncated,
    });
    Ok((dump, record))
}

/// The helper's `_find_app`: exact name first; a partial name that fits two
/// different applications is refused; among same-named processes the one
/// with the active window wins.
fn find_app<'a>(apps: &'a [App], target: &str) -> Result<(&'a App, usize), AccessibilityError> {
    let want = target.to_lowercase();
    let mut found: Vec<&App> = apps
        .iter()
        .filter(|app| app.info.name.to_lowercase() == want)
        .collect();
    if found.is_empty() && !want.is_empty() {
        found = apps
            .iter()
            .filter(|app| app.info.name.to_lowercase().contains(&want))
            .collect();
        let mut names: Vec<&str> = found.iter().map(|app| app.info.name.as_str()).collect();
        names.sort_unstable();
        names.dedup();
        if names.len() > 1 {
            return Err(AccessibilityError::new(
                "ELEMENT_AMBIGUOUS",
                format!(
                    "{} birden fazla uygulamaya uyuyor: {}. Tam adi verin.",
                    quoted(target),
                    names.join(", ")
                ),
            ));
        }
    }
    if found.is_empty() {
        let mut names: Vec<&str> = apps
            .iter()
            .map(|app| app.info.name.as_str())
            .filter(|name| !name.is_empty())
            .collect();
        names.sort_unstable();
        names.dedup();
        return Err(AccessibilityError::new(
            "TARGET_MISMATCH",
            format!(
                "Uygulama bulunamadi: {}. Acik olanlar: {}",
                quoted(target),
                names.join(", ")
            ),
        ));
    }
    let count = found.len();
    let chosen = found
        .iter()
        .find(|app| app.active().is_some())
        .copied()
        .unwrap_or(found[0]);
    Ok((chosen, count))
}

/// Every window of every application: the helper's `windows` command. Two
/// levels only, never a walk.
pub fn windows<T: Tree + ?Sized>(tree: &T) -> Result<Value, AccessibilityError> {
    let apps = read_apps(tree)?;
    let mut out = Vec::new();
    for app in &apps {
        let pid = tree.pid(&app.reference.bus);
        for (index, window, info) in &app.windows {
            out.push(json!({
                "app": app.info.name,
                "app_bus": app.reference.bus,
                "app_pid": pid,
                "window": info.name,
                "ref": window.path,
                "index": index,
                "role": info.role,
                "active": info.states.contains(STATE_ACTIVE),
                "children": info.children.len(),
            }));
        }
    }
    Ok(json!({"ok": true, "windows": out}))
}

/// The focused window, without walking anything below it.
pub fn focused<T: Tree + ?Sized>(tree: &T) -> Result<Value, AccessibilityError> {
    let apps = read_apps(tree)?;
    let Some((app, (_, window, info))) = apps
        .iter()
        .find_map(|app| app.active().map(|window| (app, window)))
    else {
        return Err(AccessibilityError::new(
            "TARGET_MISMATCH",
            "Odakta pencere yok (AT-SPI hicbir pencereyi ACTIVE isaretlemiyor).",
        ));
    };
    Ok(json!({
        "ok": true,
        "app": app.info.name,
        "app_bus": app.reference.bus,
        "app_pid": tree.pid(&app.reference.bus),
        "window": info.name,
        "window_ref": window.path,
    }))
}
