//! The parameters of the `accessibility.*` requests, the dumps a helper
//! keeps for the actions that name them, and the error envelope: shared by
//! the Linux and Windows dispatchers (docs/native/protocol-v1.md).

use std::collections::VecDeque;
use std::collections::hash_map::RandomState;
use std::hash::{BuildHasher, Hasher};
use std::time::{Duration, Instant};

use pcbridge_core::ErrorBody;
use serde::Deserialize;
use serde_json::Value;

use super::action::Target;
use super::{AccessibilityError, DumpRecord, FocusHint, NodeRecord, Tree};

/// The dumps this helper made, newest last (Task 6.3).
///
/// An action names a dump by the snapshot id that dump returned, and a node
/// by its object path. The helper acts only on what it listed itself: an id
/// from another helper, another process or an older grant is unknown here.
#[derive(Debug, Default)]
pub(crate) struct DumpRegistry {
    pub(crate) dumps: VecDeque<(String, DumpRecord)>,
    /// Seeded from the operating system once per helper, so ids differ
    /// between helpers and between runs.
    pub(crate) random: RandomState,
    pub(crate) issued: u64,
}

/// Dumps kept for actions. The host acts on its last dump; the few before
/// it cover a dump that lands while an action on the previous one runs.
pub(crate) const KEPT_DUMPS: usize = 8;

impl DumpRegistry {
    /// Keep `record` and return its snapshot id: 12 hex digits, the length
    /// the Python reader's ids have.
    pub(crate) fn insert(&mut self, record: DumpRecord) -> String {
        let id = loop {
            self.issued += 1;
            let mut hasher = self.random.build_hasher();
            hasher.write_u64(self.issued);
            let id = format!("{:012x}", hasher.finish() & 0xffff_ffff_ffff);
            if self.dumps.iter().all(|(known, _)| *known != id) {
                break id;
            }
        };
        self.dumps.push_back((id.clone(), record));
        while self.dumps.len() > KEPT_DUMPS {
            self.dumps.pop_front();
        }
        id
    }

    /// The dump and the node an action names.
    pub(crate) fn node(
        &self,
        snapshot: &str,
        reference: &str,
    ) -> Result<(DumpRecord, NodeRecord), AccessibilityError> {
        let Some((_, record)) = self.dumps.iter().find(|(id, _)| id == snapshot) else {
            return Err(AccessibilityError {
                code: "ELEMENT_STALE",
                message: "This helper does not hold that list (the grant may have been renewed \
                          or the helper restarted). Nothing was done; refresh the list with \
                          ui_dump."
                    .to_owned(),
            });
        };
        let Some(node) = record.nodes.get(reference) else {
            return Err(AccessibilityError {
                code: "ELEMENT_STALE",
                message: "That element is not in that list. Nothing was done; refresh the \
                          list with ui_dump."
                    .to_owned(),
            });
        };
        Ok((record.clone(), node.clone()))
    }
}

/// A dump gives up after this long unless the request asks for less; the
/// host's own request timeout is longer, so the reason arrives as TIMEOUT.
pub(crate) const DUMP_DEADLINE_MS: u64 = 15_000;
/// Finding an action's target again gives up after this long, before
/// anything is sent. With the call itself (`bus::ACTION_TIMEOUT`) and the
/// text read back it stays under the host's 20 s.
pub(crate) const RESOLVE_DEADLINE_MS: u64 = 8_000;

pub(crate) fn action_target<'a>(record: &'a DumpRecord, node: &'a NodeRecord) -> Target<'a> {
    Target {
        record,
        node,
        deadline: Some(Instant::now() + Duration::from_millis(RESOLVE_DEADLINE_MS)),
        deadline_seconds: RESOLVE_DEADLINE_MS / 1000,
    }
}
pub(crate) const DUMP_DEADLINE_MS_MAX: u64 = 20_000;
/// The largest list one dump may return (the walk visits 25 times more).
pub(crate) const MAX_DUMP_NODES: u64 = 2_000;

pub(crate) fn accessibility_error_body(error: &AccessibilityError) -> ErrorBody {
    let (category, retryable) = match error.code {
        "ELEMENT_STALE" | "ELEMENT_AMBIGUOUS" | "TARGET_MISMATCH" => ("accessibility", true),
        "ACTION_UNSUPPORTED" | "TEXT_MISMATCH" => ("accessibility", false),
        "TIMEOUT" => ("execution", true),
        // Sent and not answered: it may have happened, so it is not retried.
        "EXECUTION_UNKNOWN" => ("execution", false),
        _ => ("capability", true),
    };
    ErrorBody {
        code: error.code.to_owned(),
        message: error.message.clone(),
        retryable,
        category: category.to_owned(),
    }
}

pub(crate) trait AccessibilityGrant {
    fn grant_id(&self) -> &str;
    fn revoke_epoch(&self) -> u64;
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct AccessibilityGrantParams {
    pub(crate) grant_id: String,
    pub(crate) revoke_epoch: u64,
    #[serde(default)]
    pub(crate) focus: Option<FocusParams>,
}

/// The compositor's focused window, where the host can ask it and the
/// helper cannot (GNOME's shell extension): its title and the names of its
/// application. Sent with `accessibility.focus_hint`; an X11 answer the
/// helper reads itself wins over it.
#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct FocusParams {
    #[serde(default)]
    pub(crate) title: String,
    #[serde(default)]
    pub(crate) apps: Vec<String>,
}

/// Longest title and application name taken from a focus hint, and how many
/// names: the extension sends at most 200 characters and two names.
pub(crate) const FOCUS_HINT_FIELD_MAX: usize = 512;
pub(crate) const FOCUS_HINT_APPS_MAX: usize = 4;

impl FocusParams {
    pub(crate) fn hint(&self) -> FocusHint {
        let cut = |text: &str| text.chars().take(FOCUS_HINT_FIELD_MAX).collect::<String>();
        FocusHint {
            pid: 0,
            title: cut(&self.title),
            apps: self
                .apps
                .iter()
                .map(|name| cut(name))
                .filter(|name| !name.trim().is_empty())
                .take(FOCUS_HINT_APPS_MAX)
                .collect(),
        }
    }
}

/// A read of the tree, given the focus hint the request carried.
pub(crate) type AccessibilityRead =
    dyn Fn(&dyn Tree, Option<&FocusHint>) -> Result<Value, AccessibilityError>;

pub(crate) fn default_target() -> String {
    "focused".to_owned()
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct AccessibilityDumpParams {
    pub(crate) grant_id: String,
    pub(crate) revoke_epoch: u64,
    #[serde(default = "default_target")]
    pub(crate) target: String,
    #[serde(default = "default_true")]
    pub(crate) interactive_only: bool,
    /// 0 means the default, as in the Python helper.
    #[serde(default)]
    pub(crate) max_nodes: u64,
    /// 0 means the default.
    #[serde(default)]
    pub(crate) deadline_ms: u64,
    #[serde(default)]
    pub(crate) focus: Option<FocusParams>,
}

pub(crate) fn default_action() -> String {
    "click".to_owned()
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct AccessibilityActParams {
    pub(crate) grant_id: String,
    pub(crate) revoke_epoch: u64,
    /// The dump the node was listed in, as that dump returned it.
    pub(crate) snapshot: String,
    /// The node's object path, the `ref` of its dump entry.
    #[serde(rename = "ref")]
    pub(crate) reference: String,
    #[serde(default = "default_action")]
    pub(crate) action: String,
}

/// The text travels in the binary payload.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct AccessibilitySetTextParams {
    pub(crate) grant_id: String,
    pub(crate) revoke_epoch: u64,
    pub(crate) snapshot: String,
    #[serde(rename = "ref")]
    pub(crate) reference: String,
}

impl AccessibilityGrant for AccessibilityActParams {
    fn grant_id(&self) -> &str {
        &self.grant_id
    }

    fn revoke_epoch(&self) -> u64 {
        self.revoke_epoch
    }
}

impl AccessibilityGrant for AccessibilitySetTextParams {
    fn grant_id(&self) -> &str {
        &self.grant_id
    }

    fn revoke_epoch(&self) -> u64 {
        self.revoke_epoch
    }
}

impl AccessibilityGrant for AccessibilityGrantParams {
    fn grant_id(&self) -> &str {
        &self.grant_id
    }

    fn revoke_epoch(&self) -> u64 {
        self.revoke_epoch
    }
}

impl AccessibilityGrant for AccessibilityDumpParams {
    fn grant_id(&self) -> &str {
        &self.grant_id
    }

    fn revoke_epoch(&self) -> u64 {
        self.revoke_epoch
    }
}

const fn default_true() -> bool {
    true
}

#[cfg(test)]
mod tests {
    use super::super::ObjectRef;
    use super::*;

    fn record(path: &str) -> DumpRecord {
        let node = NodeRecord {
            object: ObjectRef::new(":1.30", path),
            path: vec![0, 1],
            role: "push button".to_owned(),
            name: "Tamam".to_owned(),
            shared: false,
        };
        DumpRecord {
            app: "pcbridge-editor".to_owned(),
            app_bus: ":1.30".to_owned(),
            window: None,
            nodes: std::iter::once((path.to_owned(), node)).collect(),
        }
    }

    #[test]
    fn actions_name_only_dumps_this_helper_made() {
        let mut registry = DumpRegistry::default();
        let first = registry.insert(record("/a"));
        assert_eq!(first.len(), 12);
        assert!(first.chars().all(|c| c.is_ascii_hexdigit()));
        let (_, node) = registry.node(&first, "/a").expect("its own dump");
        assert_eq!(node.name, "Tamam");
        // A node of another dump, an unknown id, an id of another helper.
        assert_eq!(
            registry.node(&first, "/b").unwrap_err().code,
            "ELEMENT_STALE"
        );
        assert_eq!(
            registry.node("000000000000", "/a").unwrap_err().code,
            "ELEMENT_STALE"
        );
        let other = DumpRegistry::default().insert(record("/a"));
        assert_ne!(other, first, "two helpers issue different ids");
        assert_eq!(
            registry.node(&other, "/a").unwrap_err().code,
            "ELEMENT_STALE"
        );
    }

    #[test]
    fn only_the_last_dumps_are_kept() {
        let mut registry = DumpRegistry::default();
        let ids: Vec<String> = (0..=KEPT_DUMPS)
            .map(|_| registry.insert(record("/a")))
            .collect();
        let unique: std::collections::HashSet<&String> = ids.iter().collect();
        assert_eq!(unique.len(), ids.len());
        assert!(registry.node(&ids[0], "/a").is_err(), "the oldest is gone");
        for id in &ids[1..] {
            assert!(registry.node(id, "/a").is_ok());
        }
    }
}
