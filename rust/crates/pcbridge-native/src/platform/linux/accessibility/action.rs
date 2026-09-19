//! Acting on a listed element (Task 6.3): the Python helper's `_resolve`,
//! `cmd_act` and `cmd_settext`, over the same [`Tree`].
//!
//! An action names a node of a dump this helper made ([`DumpRecord`]). Three
//! checks pass before anything is sent, the helper's three:
//!
//! 1. The same application: the bus name of the dump. A closed application
//!    is never replaced by another one.
//! 2. The same object: the index path first, then the object itself within
//!    the same target (the dumped window, or the whole application for a
//!    named dump). Identity is the bus name and the object path together:
//!    on D-Bus that pair is one object, so meeting it twice in a search is
//!    meeting the same object again, not a second candidate. The Python
//!    helper compares the path alone and refuses a path it meets twice; for
//!    every node on its application's own bus the two agree. What stays
//!    ambiguous is one path on two buses in one dump ([`NodeRecord::shared`]).
//! 3. The same meaning: role and name as dumped.
//!
//! Then the call itself, whose answer is checked too (measured 2026-09-19,
//! GTK4 4.14 test window):
//!
//! - `DoAction` on an insensitive button answers `false` and nothing is
//!   clicked. That is an error, not a success.
//! - `SetTextContents` into a field that keeps at most 5 characters answers
//!   `true` and keeps 5. The answer proves nothing, so the text is read back
//!   and compared whole.
//! - The text is written with `SetTextContents`, not `DeleteText` and
//!   `InsertText`: GTK4's entry ignores `InsertText`'s length (a length of 2
//!   inserted all 3 characters of "ğüş"), while libatspi documents it in
//!   bytes and the Python helper's byte count exists for a text view that
//!   does honor it. Replacing the whole text needs no length at all.
//!
//! A call that was sent and not answered may have happened. It is reported
//! as `EXECUTION_UNKNOWN` and never sent again.

use std::collections::HashMap;
use std::time::{Duration, Instant};

use serde_json::{Value, json};

use super::{
    AccessibilityError, CallError, DumpRecord, MAX_DEPTH, NodeInfo, NodeRecord, ObjectRef, Tree,
    quoted,
};

/// Nodes a moved element is looked for among: the dump's own visit cap
/// (`DEFAULT_MAX_NODES * VISIT_FACTOR`), so anything a dump listed is found.
pub const SEARCH_LIMIT: usize = 10_000;
/// How long a written text may take to read back whole. GTK4 answers at
/// once; a toolkit that applies the text later gets this long.
pub const TEXT_SETTLE: Duration = Duration::from_millis(300);
const TEXT_POLL: Duration = Duration::from_millis(50);
/// Actions tried after the one asked for, in this order; else the first.
const FALLBACK_ACTIONS: [&str; 4] = ["click", "press", "activate", "jump"];

const EDITABLE_TEXT: &str = "org.a11y.atspi.EditableText";
const TEXT: &str = "org.a11y.atspi.Text";

/// The node an action is for, as its dump recorded it.
#[derive(Clone, Copy, Debug)]
pub struct Target<'a> {
    pub record: &'a DumpRecord,
    pub node: &'a NodeRecord,
    /// Resolving gives up after this; nothing has been sent by then.
    pub deadline: Option<Instant>,
    pub deadline_seconds: u64,
}

/// A target found and checked, ready for the call.
#[derive(Clone, Debug)]
pub struct Resolved {
    pub object: ObjectRef,
    pub info: NodeInfo,
    /// "path" when the index path still led to it, "moved" otherwise.
    pub how: &'static str,
    /// The application's name now, as the helper reports it.
    pub app: String,
}

/// A click, resolved: the action index chosen and its name.
#[derive(Clone, Debug)]
pub struct PreparedAct {
    pub resolved: Resolved,
    pub index: i32,
    pub action: String,
}

/// A text write, resolved: whether the text can be read back, and how long
/// it was before.
#[derive(Clone, Debug)]
pub struct PreparedText {
    pub resolved: Resolved,
    pub readable: bool,
    pub old_chars: i64,
}

fn stale(message: String) -> AccessibilityError {
    AccessibilityError::new("ELEMENT_STALE", message)
}

fn unsupported(message: String) -> AccessibilityError {
    AccessibilityError::new("ACTION_UNSUPPORTED", message)
}

fn deadline_passed(target: &Target<'_>) -> Result<(), AccessibilityError> {
    match target.deadline {
        Some(deadline) if Instant::now() > deadline => {
            Err(AccessibilityError::timeout(target.deadline_seconds))
        }
        _ => Ok(()),
    }
}

/// A call sent and not answered: it may have happened, so it is not sent
/// again and the caller is told to look first.
fn unknown(what: &str) -> AccessibilityError {
    AccessibilityError::new(
        "EXECUTION_UNKNOWN",
        format!(
            "{what} gonderildi ama uygulama cevap vermedi; yapilmis da olabilir, \
             yapilmamis da. Tekrarlanmadi: once ui_dump ya da screen_capture ile sonuca \
             bakin."
        ),
    )
}

/// A read before anything was sent got no answer.
fn no_answer() -> AccessibilityError {
    AccessibilityError::new(
        "TIMEOUT",
        "Uygulama cevap vermedi. Donmus olabilir; ekran goruntusuyle bakin \
         (screen_capture). Hicbir sey yapilmadi.",
    )
}

fn read_node<T: Tree + ?Sized>(tree: &T, node: &ObjectRef) -> Option<NodeInfo> {
    tree.read(std::slice::from_ref(node))
        .into_iter()
        .next()
        .flatten()
}

/// A node, or what a failed read looks like in the helper: role "?".
fn read_or_unknown<T: Tree + ?Sized>(tree: &T, node: &ObjectRef) -> NodeInfo {
    read_node(tree, node).unwrap_or_else(|| NodeInfo {
        role: "?".to_owned(),
        ..NodeInfo::default()
    })
}

/// Follow child indexes from `root`: the helper's `_node_at`.
fn node_at<T: Tree + ?Sized>(
    tree: &T,
    root: &ObjectRef,
    root_info: &NodeInfo,
    path: &[usize],
    target: &Target<'_>,
) -> Result<Option<(ObjectRef, NodeInfo)>, AccessibilityError> {
    let mut node = root.clone();
    let mut info = root_info.clone();
    for index in path {
        deadline_passed(target)?;
        let Some(Some(child)) = info.children.get(*index).cloned() else {
            return Ok(None);
        };
        let Some(child_info) = read_node(tree, &child) else {
            return Ok(None);
        };
        node = child;
        info = child_info;
    }
    Ok(Some((node, info)))
}

/// Find `want` under `scope`: the helper's `_find_ref`, with its limits.
/// The first meeting is the object; see the module notes for why a second
/// one is not a second candidate.
fn find_object<T: Tree + ?Sized>(
    tree: &T,
    scope: &ObjectRef,
    scope_info: &NodeInfo,
    want: &ObjectRef,
    target: &Target<'_>,
) -> Result<Option<(ObjectRef, NodeInfo)>, AccessibilityError> {
    let mut known: HashMap<ObjectRef, NodeInfo> = HashMap::new();
    known.insert(scope.clone(), scope_info.clone());
    let mut stack = vec![(scope.clone(), 0usize)];
    let mut seen = 0usize;
    while let Some((node, depth)) = stack.pop() {
        seen += 1;
        if seen > SEARCH_LIMIT {
            break;
        }
        if depth > MAX_DEPTH {
            continue;
        }
        deadline_passed(target)?;
        let info = match known.remove(&node) {
            Some(info) => info,
            None => read_or_unknown(tree, &node),
        };
        if node == *want {
            return Ok(Some((node, info)));
        }
        let children: Vec<ObjectRef> = info.children.iter().flatten().cloned().collect();
        // One round of concurrent reads per node, as in the walk.
        let unread: Vec<ObjectRef> = children
            .iter()
            .filter(|child| !known.contains_key(*child))
            .cloned()
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
        for child in children.into_iter().rev() {
            stack.push((child, depth + 1));
        }
    }
    Ok(None)
}

/// Find the dumped node again, or refuse: the helper's `_resolve`.
pub fn resolve<T: Tree + ?Sized>(
    tree: &T,
    target: &Target<'_>,
) -> Result<Resolved, AccessibilityError> {
    let record = target.record;
    let want = target.node;
    let label = format!("{} {}", want.role, quoted(&want.name));
    // The Python side shows a nameless application as "?".
    let app_label = quoted(if record.app.is_empty() {
        "?"
    } else {
        &record.app
    });
    if want.shared {
        // One path, two objects on two buses: the path the host sends names
        // neither of them alone.
        return Err(AccessibilityError::new(
            "ELEMENT_AMBIGUOUS",
            "Bu uygulama iki dugume ayni kimligi veriyor; hangisinin kastedildigi \
             bilinemez. Ekrana bakip (screen_capture) `mouse` kullanin.",
        ));
    }

    let app = tree
        .applications()?
        .into_iter()
        .find(|app| app.bus == record.app_bus);
    let app_info = app.as_ref().and_then(|app| read_node(tree, app));
    let (Some(app), Some(app_info)) = (app, app_info) else {
        return Err(stale(format!(
            "{app_label} artik acik degil (kapanmis ya da yeniden baslamis). Baska bir \
             uygulamaya dusulmedi; ui_dump ile listeyi yenileyin."
        )));
    };
    deadline_passed(target)?;

    let scope = match &record.window {
        None => (app.clone(), app_info.clone()),
        Some(window) => {
            let found = app_info
                .children
                .iter()
                .flatten()
                .find(|child| *child == window)
                .and_then(|child| read_node(tree, child).map(|info| (child.clone(), info)));
            found.ok_or_else(|| {
                stale(format!(
                    "Dokumdeki {app_label} penceresi kapanmis; ui_dump ile listeyi yenileyin."
                ))
            })?
        }
    };

    let mut how = "path";
    let mut found = node_at(tree, &app, &app_info, &want.path, target)?
        .filter(|(node, _)| *node == want.object);
    if found.is_none() {
        how = "moved";
        found = find_object(tree, &scope.0, &scope.1, &want.object, target)?;
    }
    let Some((object, info)) = found else {
        return Err(stale(format!(
            "Hedef artik yok: {label}. Arayuz yeniden cizilmis olabilir; ui_dump ile \
             listeyi yenileyin."
        )));
    };
    if info.role != want.role || info.name != want.name {
        return Err(AccessibilityError::new(
            "TARGET_MISMATCH",
            format!(
                "Hedef degismis: {label} simdi {} {}. Hicbir sey yapilmadi; ui_dump ile \
                 listeyi yenileyin.",
                info.role,
                quoted(&info.name)
            ),
        ));
    }
    Ok(Resolved {
        object,
        info,
        how,
        app: app_info.name,
    })
}

/// Resolve and choose the action: the one asked for, else a usual one, else
/// the first. Nothing is sent yet.
pub fn prepare_act<T: Tree + ?Sized>(
    tree: &T,
    target: &Target<'_>,
    action: &str,
) -> Result<PreparedAct, AccessibilityError> {
    let resolved = resolve(tree, target)?;
    let names: Vec<String> = resolved
        .info
        .actions
        .iter()
        .map(|name| name.to_lowercase())
        .collect();
    if names.is_empty() {
        return Err(unsupported(format!(
            "{} {} bir eylem sunmuyor (Action arayuzu yok). Koordinatla tiklamaya dusulmedi.",
            resolved.info.role,
            quoted(&resolved.info.name)
        )));
    }
    let want = match action.trim() {
        "" => "click".to_owned(),
        action => action.to_lowercase(),
    };
    let index = std::iter::once(want.as_str())
        .chain(FALLBACK_ACTIONS)
        .find_map(|candidate| names.iter().position(|name| name == candidate))
        .unwrap_or(0);
    let action = match names[index].as_str() {
        "" => format!("action{index}"),
        name => name.to_owned(),
    };
    Ok(PreparedAct {
        resolved,
        index: i32::try_from(index).unwrap_or(i32::MAX),
        action,
    })
}

/// Send the action once and check the application's answer.
pub fn perform_act<T: Tree + ?Sized>(
    tree: &T,
    prepared: PreparedAct,
) -> Result<Value, AccessibilityError> {
    let PreparedAct {
        resolved,
        index,
        action,
    } = prepared;
    match tree.do_action(&resolved.object, index) {
        Ok(true) => {}
        Ok(false) => {
            return Err(unsupported(format!(
                "Uygulama {} eylemini yapmadi: {} {} su an devre disi olabilir. Hicbir sey \
                 yapilmadi.",
                quoted(&action),
                resolved.info.role,
                quoted(&resolved.info.name)
            )));
        }
        Err(CallError::Refused(error)) => {
            return Err(unsupported(format!("Eylem calistirilamadi: {error}")));
        }
        Err(CallError::Timeout | CallError::Lost(_)) => return Err(unknown("Eylem")),
    }
    Ok(json!({
        "ok": true,
        "app": resolved.app,
        "ref": resolved.object.path,
        "role": resolved.info.role,
        "name": resolved.info.name,
        "action": action,
        "resolved_by": resolved.how,
        "returned": true,
    }))
}

/// Resolve and read what the write needs. Nothing is sent yet.
pub fn prepare_set_text<T: Tree + ?Sized>(
    tree: &T,
    target: &Target<'_>,
) -> Result<PreparedText, AccessibilityError> {
    let resolved = resolve(tree, target)?;
    let interfaces = match tree.interfaces(&resolved.object) {
        Ok(interfaces) => interfaces,
        Err(CallError::Timeout) => return Err(no_answer()),
        Err(CallError::Refused(_) | CallError::Lost(_)) => Vec::new(),
    };
    if !interfaces.iter().any(|name| name == EDITABLE_TEXT) {
        return Err(unsupported(format!(
            "{} {} duzenlenebilir degil (EditableText arayuzu yok).",
            resolved.info.role,
            quoted(&resolved.info.name)
        )));
    }
    let readable = interfaces.iter().any(|name| name == TEXT);
    let old_chars = if readable {
        match tree.character_count(&resolved.object) {
            Ok(count) => i64::from(count.max(0)),
            Err(CallError::Timeout) => return Err(no_answer()),
            Err(CallError::Refused(error) | CallError::Lost(error)) => {
                return Err(unsupported(format!(
                    "Metin yazilamadi: {error}. Hicbir sey yazilmadi."
                )));
            }
        }
    } else {
        0
    };
    Ok(PreparedText {
        resolved,
        readable,
        old_chars,
    })
}

/// Replace the text once, then read it back until it is whole or
/// [`TEXT_SETTLE`] passes. The text never appears in a message: only counts.
pub fn perform_set_text<T: Tree + ?Sized>(
    tree: &T,
    prepared: PreparedText,
    text: &str,
    settle: Duration,
) -> Result<Value, AccessibilityError> {
    let PreparedText {
        resolved,
        readable,
        old_chars,
    } = prepared;
    match tree.set_text_contents(&resolved.object, text) {
        Ok(true) => {}
        Ok(false) => {
            return Err(unsupported(format!(
                "Uygulama metni kabul etmedi: {} {} su an duzenlenemiyor olabilir. Hicbir \
                 sey yazilmadi.",
                resolved.info.role,
                quoted(&resolved.info.name)
            )));
        }
        Err(CallError::Refused(error)) => {
            return Err(unsupported(format!("Metin yazilamadi: {error}")));
        }
        Err(CallError::Timeout | CallError::Lost(_)) => return Err(unknown("Metin")),
    }
    let sent = text.chars().count();
    let mut verified = false;
    if readable {
        let until = Instant::now() + settle;
        // The length of the last text read back that was not the one sent.
        let mut last: Option<usize> = None;
        loop {
            match tree.text(&resolved.object) {
                Ok(back) if back == text => {
                    verified = true;
                    break;
                }
                Ok(back) => last = Some(back.chars().count()),
                // Written but never readable: not verified, and not an error
                // for a write that happened.
                Err(_) if last.is_none() => break,
                Err(_) => {}
            }
            if let Some(now) = last
                && Instant::now() >= until
            {
                return Err(AccessibilityError::new(
                    "TEXT_MISMATCH",
                    format!(
                        "Metin eksik ya da farkli yazildi: {sent} karakter gonderildi, alanda \
                         simdi {now} karakter var. Alanin icerigi degisti; ui_dump ile bakin."
                    ),
                ));
            }
            std::thread::sleep(TEXT_POLL);
        }
    }
    let now_chars = if verified {
        i64::try_from(sent).unwrap_or(i64::MAX)
    } else {
        -1
    };
    Ok(json!({
        "ok": true,
        "app": resolved.app,
        "ref": resolved.object.path,
        "role": resolved.info.role,
        "name": resolved.info.name,
        "replaced_chars": old_chars,
        "now_chars": now_chars,
        "resolved_by": resolved.how,
        "verified": verified,
    }))
}
