//! The real tree: the AT-SPI registry on the accessibility bus, over zbus.
//!
//! The accessibility bus is its own message bus; the session bus only tells
//! where it is (`org.a11y.Bus.GetAddress`). Every node is read with plain
//! method calls, the ones libatspi itself makes, so no GI, no GTK and no GLib
//! main loop is involved.
//!
//! A hung application must not hold a request: every read has a short
//! timeout, and the walk has a deadline of its own on top. The two calls
//! that act (`DoAction`, `SetTextContents`) get longer: GTK4 runs a
//! button's handler before it answers, and a slow handler must not be
//! mistaken for a lost call more often than it has to.

use std::collections::HashMap;
use std::future::Future;
use std::io::ErrorKind;
use std::pin::Pin;
use std::sync::Mutex;
use std::task::Poll;
use std::time::Duration;

use async_io::Timer;
use futures_lite::FutureExt;
use futures_lite::future::{self, poll_fn};
use serde::de::DeserializeOwned;
use zbus::connection::Builder;
use zbus::zvariant::{DynamicType, OwnedObjectPath, OwnedValue, Type};
use zbus::{Connection, Message};

use super::{AccessibilityError, CallError, NodeInfo, ObjectRef, States, Tree, role_name};

const A11Y_BUS: &str = "org.a11y.Bus";
const A11Y_BUS_PATH: &str = "/org/a11y/bus";
const REGISTRY: &str = "org.a11y.atspi.Registry";
const DESKTOP_ROOT: &str = "/org/a11y/atspi/accessible/root";
const ACCESSIBLE: &str = "org.a11y.atspi.Accessible";
const ACTION: &str = "org.a11y.atspi.Action";
const EDITABLE_TEXT: &str = "org.a11y.atspi.EditableText";
const TEXT: &str = "org.a11y.atspi.Text";
const PROPERTIES: &str = "org.freedesktop.DBus.Properties";
/// The bus's own answer when the application left before replying.
const NO_REPLY: &str = "org.freedesktop.DBus.Error.NoReply";
/// AT-SPI's null reference: a child slot with no object behind it.
const NULL_PATH: &str = "/org/a11y/atspi/null";

/// One call to one application. libatspi waits longer, but a dump reads
/// hundreds of nodes and one frozen application must not eat the deadline.
pub const CALL_TIMEOUT: Duration = Duration::from_secs(2);
/// One call that acts. Past this the call is reported as possibly done
/// (`EXECUTION_UNKNOWN`) and never sent again. The host waits 20 s for the
/// whole request: resolving, this call and reading the text back.
pub const ACTION_TIMEOUT: Duration = Duration::from_secs(5);
/// Calls in flight at once. Enough to hide the round trips, few enough not
/// to flood the application being read.
const CONCURRENCY: usize = 32;

/// Poll every future until all are done; outputs in input order.
async fn join_all<F: Future>(futures: Vec<F>) -> Vec<F::Output> {
    let mut futures: Vec<Pin<Box<F>>> = futures.into_iter().map(Box::pin).collect();
    let mut outputs: Vec<Option<F::Output>> = futures.iter().map(|_| None).collect();
    poll_fn(|context| {
        let mut pending = false;
        for (future, output) in futures.iter_mut().zip(outputs.iter_mut()) {
            if output.is_none() {
                match future.as_mut().poll(context) {
                    Poll::Ready(value) => *output = Some(value),
                    Poll::Pending => pending = true,
                }
            }
        }
        if pending {
            Poll::Pending
        } else {
            Poll::Ready(())
        }
    })
    .await;
    outputs.into_iter().flatten().collect()
}

pub struct AtspiBus {
    connection: Connection,
    pids: Mutex<HashMap<String, u32>>,
}

impl std::fmt::Debug for AtspiBus {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.debug_struct("AtspiBus").finish_non_exhaustive()
    }
}

impl AtspiBus {
    /// Find the accessibility bus through the session bus and connect to it.
    pub fn connect() -> Result<Self, AccessibilityError> {
        let unavailable = |error: zbus::Error| {
            AccessibilityError::unavailable(format!(
                "Cannot connect to the accessibility bus: {error}"
            ))
        };
        zbus::block_on(async {
            let session = Builder::session()
                .map_err(unavailable)?
                .method_timeout(CALL_TIMEOUT)
                .build()
                .await
                .map_err(unavailable)?;
            let reply = session
                .call_method(
                    Some(A11Y_BUS),
                    A11Y_BUS_PATH,
                    Some(A11Y_BUS),
                    "GetAddress",
                    &(),
                )
                .await
                .map_err(unavailable)?;
            let address: String = reply.body().deserialize().map_err(unavailable)?;
            // Every call is bounded by its own timer (`call`); this is only
            // the outer bound for a call that forgot one.
            let connection = Builder::address(address.as_str())
                .map_err(unavailable)?
                .method_timeout(ACTION_TIMEOUT)
                .build()
                .await
                .map_err(unavailable)?;
            Ok(Self {
                connection,
                pids: Mutex::new(HashMap::new()),
            })
        })
    }

    /// One read. The body is owned so a batch of calls can be built in a
    /// closure and awaited later.
    async fn call<T, B>(
        &self,
        node: &ObjectRef,
        interface: &str,
        method: &str,
        body: B,
    ) -> Result<T, zbus::Error>
    where
        T: DeserializeOwned + Type,
        B: serde::Serialize + DynamicType,
    {
        self.timed(node, interface, method, body, CALL_TIMEOUT)
            .await
    }

    /// One call, given up after `timeout`: zbus's own timeout, per call.
    async fn timed<T, B>(
        &self,
        node: &ObjectRef,
        interface: &str,
        method: &str,
        body: B,
        timeout: Duration,
    ) -> Result<T, zbus::Error>
    where
        T: DeserializeOwned + Type,
        B: serde::Serialize + DynamicType,
    {
        let call = async {
            let reply: Message = self
                .connection
                .call_method(
                    Some(node.bus.as_str()),
                    node.path.as_str(),
                    Some(interface),
                    method,
                    &body,
                )
                .await?;
            reply.body().deserialize()
        };
        call.or(async {
            Timer::after(timeout).await;
            Err(zbus::Error::InputOutput(
                std::io::Error::new(ErrorKind::TimedOut, "timed out").into(),
            ))
        })
        .await
    }

    /// A call that acts, and what its failure means for whether it happened.
    fn act<T, B>(
        &self,
        node: &ObjectRef,
        interface: &str,
        method: &str,
        body: B,
    ) -> Result<T, CallError>
    where
        T: DeserializeOwned + Type,
        B: serde::Serialize + DynamicType,
    {
        zbus::block_on(self.timed(node, interface, method, body, ACTION_TIMEOUT))
            .map_err(call_error)
    }

    async fn property<T>(&self, node: &ObjectRef, interface: &str, name: &str) -> Option<T>
    where
        T: TryFrom<OwnedValue>,
    {
        let value: OwnedValue = self
            .call(node, PROPERTIES, "Get", (interface, name))
            .await
            .ok()?;
        T::try_from(value).ok()
    }

    fn reference((bus, path): (String, OwnedObjectPath)) -> Option<ObjectRef> {
        let path = path.as_str();
        (!bus.is_empty() && path != NULL_PATH).then(|| ObjectRef::new(bus, path))
    }

    async fn children(&self, node: &ObjectRef) -> Vec<Option<ObjectRef>> {
        let listed: Result<Vec<(String, OwnedObjectPath)>, _> =
            self.call(node, ACCESSIBLE, "GetChildren", ()).await;
        if let Ok(listed) = listed {
            return listed.into_iter().map(Self::reference).collect();
        }
        // Not every toolkit answers `GetChildren`; the per-index reads are
        // what libatspi falls back to as well.
        let count: i32 = self
            .property(node, ACCESSIBLE, "ChildCount")
            .await
            .unwrap_or(0);
        let reads = (0..count.max(0))
            .map(|index| {
                self.call::<(String, OwnedObjectPath), _>(
                    node,
                    ACCESSIBLE,
                    "GetChildAtIndex",
                    (index,),
                )
            })
            .collect();
        join_all(reads)
            .await
            .into_iter()
            .map(|read| read.ok().and_then(Self::reference))
            .collect()
    }

    async fn role(&self, node: &ObjectRef, number: Result<u32, zbus::Error>) -> String {
        // libatspi: a failed read raises and the helper shows "?"; a role
        // inside libatspi's table is named by the table, anything else by
        // the toolkit.
        let Ok(number) = number else {
            return "?".to_owned();
        };
        if let Some(name) = role_name(number) {
            return name.to_owned();
        }
        let named: Result<String, _> = self.call(node, ACCESSIBLE, "GetRoleName", ()).await;
        match named {
            Ok(name) if !name.is_empty() => name,
            _ => "?".to_owned(),
        }
    }

    /// The canonical names, all or nothing: the helper drops the whole list
    /// when any one read fails.
    async fn actions(&self, node: &ObjectRef) -> Vec<String> {
        let Some(count) = self.property::<i32>(node, ACTION, "NActions").await else {
            return Vec::new();
        };
        let reads = (0..count.max(0))
            .map(|index| self.call::<String, _>(node, ACTION, "GetName", (index,)))
            .collect();
        let names: Result<Vec<String>, _> = join_all(reads).await.into_iter().collect();
        names.unwrap_or_default()
    }

    async fn node(&self, node: &ObjectRef) -> NodeInfo {
        let ((number, name), (states, (interfaces, children))) = future::zip(
            future::zip(
                self.call::<u32, _>(node, ACCESSIBLE, "GetRole", ()),
                self.property::<String>(node, ACCESSIBLE, "Name"),
            ),
            future::zip(
                self.call::<Vec<u32>, _>(node, ACCESSIBLE, "GetState", ()),
                future::zip(
                    self.call::<Vec<String>, _>(node, ACCESSIBLE, "GetInterfaces", ()),
                    self.children(node),
                ),
            ),
        )
        .await;
        let has_action = interfaces.is_ok_and(|list| list.iter().any(|name| name == ACTION));
        let (role, actions) = future::zip(self.role(node, number), async {
            if has_action {
                self.actions(node).await
            } else {
                Vec::new()
            }
        })
        .await;
        NodeInfo {
            role,
            name: name.unwrap_or_default().trim().to_owned(),
            states: states.map_or_else(|_| States::default(), |words| States::from_words(&words)),
            actions,
            children,
        }
    }
}

/// Whether a failed call may have been carried out.
///
/// An error reply is the application's refusal, except `NoReply`, which the
/// bus sends when the application left without answering. A timeout or a
/// broken connection leaves it unknown.
fn call_error(error: zbus::Error) -> CallError {
    match error {
        zbus::Error::MethodError(name, _, _) if name.as_str() == NO_REPLY => {
            CallError::Lost(name.to_string())
        }
        zbus::Error::MethodError(name, message, _) => CallError::Refused(match message {
            Some(message) if !message.is_empty() => format!("{name}: {message}"),
            _ => name.to_string(),
        }),
        zbus::Error::InputOutput(error) if error.kind() == ErrorKind::TimedOut => {
            CallError::Timeout
        }
        other => CallError::Lost(other.to_string()),
    }
}

impl Tree for AtspiBus {
    fn applications(&self) -> Result<Vec<ObjectRef>, AccessibilityError> {
        let root = ObjectRef::new(REGISTRY, DESKTOP_ROOT);
        let listed: Vec<(String, OwnedObjectPath)> =
            zbus::block_on(self.call(&root, ACCESSIBLE, "GetChildren", ())).map_err(|error| {
                AccessibilityError::unavailable(format!(
                    "Cannot read the applications in the accessibility registry: {error}"
                ))
            })?;
        Ok(listed.into_iter().filter_map(Self::reference).collect())
    }

    fn read(&self, nodes: &[ObjectRef]) -> Vec<Option<NodeInfo>> {
        zbus::block_on(async {
            let mut out = Vec::with_capacity(nodes.len());
            for chunk in nodes.chunks(CONCURRENCY) {
                let reads = chunk.iter().map(|node| self.node(node)).collect();
                out.extend(join_all(reads).await.into_iter().map(Some));
            }
            out
        })
    }

    fn pid(&self, bus: &str) -> u32 {
        if let Some(pid) = self
            .pids
            .lock()
            .ok()
            .and_then(|pids| pids.get(bus).copied())
        {
            return pid;
        }
        let daemon = ObjectRef::new("org.freedesktop.DBus", "/org/freedesktop/DBus");
        let pid = zbus::block_on(self.call::<u32, _>(
            &daemon,
            "org.freedesktop.DBus",
            "GetConnectionUnixProcessID",
            (bus.to_owned(),),
        ))
        .unwrap_or(0);
        if pid != 0
            && let Ok(mut pids) = self.pids.lock()
        {
            pids.insert(bus.to_owned(), pid);
        }
        pid
    }

    fn interfaces(&self, node: &ObjectRef) -> Result<Vec<String>, CallError> {
        zbus::block_on(self.call(node, ACCESSIBLE, "GetInterfaces", ())).map_err(call_error)
    }

    fn do_action(&self, node: &ObjectRef, index: i32) -> Result<bool, CallError> {
        self.act(node, ACTION, "DoAction", (index,))
    }

    fn set_text_contents(&self, node: &ObjectRef, text: &str) -> Result<bool, CallError> {
        self.act(node, EDITABLE_TEXT, "SetTextContents", (text.to_owned(),))
    }

    fn character_count(&self, node: &ObjectRef) -> Result<i32, CallError> {
        zbus::block_on(async {
            let value: OwnedValue = self
                .call(node, PROPERTIES, "Get", (TEXT, "CharacterCount"))
                .await
                .map_err(call_error)?;
            i32::try_from(value).map_err(|error| CallError::Refused(error.to_string()))
        })
    }

    fn text(&self, node: &ObjectRef) -> Result<String, CallError> {
        let count = self.character_count(node)?;
        zbus::block_on(self.call(node, TEXT, "GetText", (0i32, count.max(0)))).map_err(call_error)
    }
}
