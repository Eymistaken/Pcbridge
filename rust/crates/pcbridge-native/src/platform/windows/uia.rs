//! UI Automation as the shared accessibility walk's [`Tree`].
//!
//! The walk, the dump records and the action checks are the ones Linux
//! runs over AT-SPI (`crate::accessibility`); this module answers the same
//! questions from UI Automation and speaks AT-SPI's vocabulary, which the
//! Python side and the fixtures use:
//!
//! - An application is a process with visible top-level windows
//!   (`pid:<pid>` as its "bus", named after its executable); its children
//!   are those windows (`hwnd:<handle>`), and their children are UIA's
//!   control view. An element's path is its `RuntimeId` (`rid:...`), which
//!   W0 measured stable across an edit, as AT-SPI's object path is.
//! - Control types map to libatspi role names (`Button` is "push button",
//!   `Edit` "entry" or "password text", `Document` "text").
//! - Patterns become actions: Invoke "click", Toggle "toggle",
//!   SelectionItem "select", ExpandCollapse "expand or collapse". A
//!   writable ValuePattern is EditableText; Value or Text is Text.
//!
//! An element found once is kept by its path, so the node the walk lists
//! and the node an action reads again are the same COM object. Windows'
//! own UIPI hides elevated windows from an unelevated client entirely
//! (measured in W0), so they never appear here.

use std::cell::RefCell;
use std::collections::HashMap;

use windows::Win32::Foundation::{BOOL, HWND, LPARAM, RECT};
use windows::Win32::Graphics::Dwm::{DWMWA_CLOAKED, DwmGetWindowAttribute};
use windows::Win32::System::Com::{
    CLSCTX_INPROC_SERVER, COINIT_MULTITHREADED, CoCreateInstance, CoInitializeEx, SAFEARRAY,
};
use windows::Win32::System::Ole::{
    SafeArrayDestroy, SafeArrayGetElement, SafeArrayGetLBound, SafeArrayGetUBound,
};
use windows::Win32::UI::Accessibility::{
    CUIAutomation, ExpandCollapseState_Collapsed, ExpandCollapseState_Expanded,
    ExpandCollapseState_PartiallyExpanded, IUIAutomation, IUIAutomation2, IUIAutomationElement,
    IUIAutomationExpandCollapsePattern, IUIAutomationInvokePattern,
    IUIAutomationSelectionItemPattern, IUIAutomationTextPattern, IUIAutomationTogglePattern,
    IUIAutomationTreeWalker, IUIAutomationValuePattern, ToggleState_On, UIA_CONTROLTYPE_ID,
    UIA_ExpandCollapsePatternId, UIA_InvokePatternId, UIA_SelectionItemPatternId,
    UIA_TextPatternId, UIA_TogglePatternId, UIA_ValuePatternId,
};
use windows::Win32::UI::WindowsAndMessaging::{
    EnumWindows, GWL_EXSTYLE, GetForegroundWindow, GetWindowLongPtrW, GetWindowTextLengthW,
    GetWindowTextW, GetWindowThreadProcessId, IsWindowVisible, WS_EX_TOOLWINDOW,
};
use windows::core::{BSTR, Interface};

use super::process;
use crate::accessibility::{
    AccessibilityError, CallError, NodeInfo, ObjectRef, States, Tree, state_bit,
};

pub const BACKEND: &str = "windows.uia";

const EDITABLE_TEXT: &str = "org.a11y.atspi.EditableText";
const TEXT: &str = "org.a11y.atspi.Text";
/// How long one UIA call may take before it is a timeout (the 5 s the
/// Linux bus allows an action).
const CALL_TIMEOUT_MS: u32 = 5_000;
/// `UIA_E_TIMEOUT`.
const UIA_E_TIMEOUT: i32 = 0x8013_1505_u32 as i32;
/// `UIA_E_ELEMENTNOTAVAILABLE`: the element is gone.
const UIA_E_ELEMENTNOTAVAILABLE: i32 = 0x8004_0201_u32 as i32;

/// A top-level window an application owns, as listed.
#[derive(Clone, Copy, Debug)]
pub struct TopWindow {
    pub hwnd: HWND,
    pub pid: u32,
}

/// A visible, uncloaked top-level window with a title that is not a tool
/// window: what the taskbar and Alt+Tab show, in z-order.
#[must_use]
pub fn top_windows() -> Vec<TopWindow> {
    unsafe extern "system" fn collect(hwnd: HWND, data: LPARAM) -> BOOL {
        // SAFETY: `data` is the Vec `top_windows` passed in, alive for the
        // whole enumeration, which calls back on this thread.
        let found = unsafe { &mut *(data.0 as *mut Vec<TopWindow>) };
        if is_listed(hwnd) {
            let mut pid = 0u32;
            // SAFETY: an out-parameter this frame owns.
            unsafe { GetWindowThreadProcessId(hwnd, Some(&mut pid)) };
            found.push(TopWindow { hwnd, pid });
        }
        BOOL::from(true)
    }
    let mut found: Vec<TopWindow> = Vec::new();
    // SAFETY: the callback only touches `found`, which outlives the call.
    let _ = unsafe { EnumWindows(Some(collect), LPARAM(&raw mut found as isize)) };
    found
}

fn is_listed(hwnd: HWND) -> bool {
    // SAFETY: reads of a window handle Windows gave us.
    unsafe {
        if !IsWindowVisible(hwnd).as_bool() || GetWindowTextLengthW(hwnd) == 0 {
            return false;
        }
        if GetWindowLongPtrW(hwnd, GWL_EXSTYLE) as u32 & WS_EX_TOOLWINDOW.0 != 0 {
            return false;
        }
    }
    let mut cloaked = 0u32;
    // SAFETY: a u32 out-parameter of the size given.
    let read = unsafe {
        DwmGetWindowAttribute(
            hwnd,
            DWMWA_CLOAKED,
            (&raw mut cloaked).cast(),
            std::mem::size_of::<u32>() as u32,
        )
    };
    read.is_err() || cloaked == 0
}

#[must_use]
pub fn window_title(hwnd: HWND) -> String {
    let mut buffer = [0u16; 512];
    // SAFETY: a buffer of the length given.
    let length = unsafe { GetWindowTextW(hwnd, &mut buffer) };
    String::from_utf16_lossy(&buffer[..usize::try_from(length).unwrap_or(0)])
}

fn hwnd_path(hwnd: HWND) -> String {
    format!("hwnd:{:x}", hwnd.0 as usize)
}

fn parse_hwnd(path: &str) -> Option<HWND> {
    let value = usize::from_str_radix(path.strip_prefix("hwnd:")?, 16).ok()?;
    Some(HWND(value as *mut core::ffi::c_void))
}

fn bus_of(pid: u32) -> String {
    format!("pid:{pid}")
}

fn parse_pid(bus: &str) -> Option<u32> {
    bus.strip_prefix("pid:")?.parse().ok()
}

/// libatspi's role name for a UIA control type.
fn role_of(control: UIA_CONTROLTYPE_ID, password: bool) -> &'static str {
    match control.0 {
        50000 | 50031 => "push button",
        50001 => "calendar",
        50002 => "check box",
        50003 => "combo box",
        50004 if password => "password text",
        50004 => "entry",
        50005 => "link",
        50006 => "image",
        50007 => "list item",
        50008 => "list",
        50009 => "menu",
        50010 => "menu bar",
        50011 => "menu item",
        50012 => "progress bar",
        50013 => "radio button",
        50014 => "scroll bar",
        50015 => "slider",
        50016 => "spin button",
        50017 => "status bar",
        50018 => "page tab list",
        50019 => "page tab",
        50020 => "label",
        50021 | 50040 => "tool bar",
        50022 => "tool tip",
        50023 => "tree",
        50024 => "tree item",
        50026 => "grouping",
        50028 | 50036 => "table",
        50029 => "table cell",
        50030 => "text",
        50032 => "frame",
        50033 | 50039 => "panel",
        50034 => "header",
        50035 => "column header",
        50037 => "title bar",
        50038 => "separator",
        _ => "unknown",
    }
}

fn bit(name: &str) -> u32 {
    state_bit(name).expect("an AT-SPI state name")
}

/// A RuntimeId as a path: `rid:` and its integers.
fn runtime_path(element: &IUIAutomationElement) -> Option<String> {
    // SAFETY: a COM call on an element this module holds.
    let array: *mut SAFEARRAY = unsafe { element.GetRuntimeId() }.ok()?;
    if array.is_null() {
        return None;
    }
    // SAFETY: a one-dimensional SAFEARRAY of VT_I4 that UIA returned; it is
    // read element by element and destroyed once.
    let parts = unsafe {
        let low = SafeArrayGetLBound(array, 1).ok();
        let high = SafeArrayGetUBound(array, 1).ok();
        let mut parts = Vec::new();
        if let (Some(low), Some(high)) = (low, high) {
            for index in low..=high {
                let mut value = 0i32;
                if SafeArrayGetElement(array, &index, (&raw mut value).cast()).is_ok() {
                    parts.push(value.to_string());
                }
            }
        }
        let _ = SafeArrayDestroy(array);
        parts
    };
    (!parts.is_empty()).then(|| format!("rid:{}", parts.join(".")))
}

fn call_error(error: &windows::core::Error) -> CallError {
    match error.code().0 {
        UIA_E_TIMEOUT => CallError::Timeout,
        UIA_E_ELEMENTNOTAVAILABLE => CallError::Lost(error.message()),
        _ => CallError::Refused(error.message()),
    }
}

/// UI Automation, connected once per helper.
pub struct Uia {
    automation: IUIAutomation,
    walker: IUIAutomationTreeWalker,
    elements: RefCell<HashMap<String, IUIAutomationElement>>,
}

impl std::fmt::Debug for Uia {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("Uia(..)")
    }
}

impl Uia {
    pub fn connect() -> Result<Self, AccessibilityError> {
        // SAFETY: joins this thread to the multithreaded apartment, which UI
        // Automation clients use; a second call on the thread is harmless.
        let _ = unsafe { CoInitializeEx(None, COINIT_MULTITHREADED) };
        // SAFETY: creates the in-process UIA client object.
        let automation: IUIAutomation =
            unsafe { CoCreateInstance(&CUIAutomation, None, CLSCTX_INPROC_SERVER) }.map_err(
                |error| AccessibilityError::unavailable(format!("UI Automation: {error}")),
            )?;
        if let Ok(timeouts) = automation.cast::<IUIAutomation2>() {
            // SAFETY: plain setters on the client object.
            unsafe {
                let _ = timeouts.SetConnectionTimeout(CALL_TIMEOUT_MS);
                let _ = timeouts.SetTransactionTimeout(CALL_TIMEOUT_MS);
            }
        }
        // SAFETY: a getter on the client object.
        let walker = unsafe { automation.ControlViewWalker() }
            .map_err(|error| AccessibilityError::unavailable(format!("UI Automation: {error}")))?;
        Ok(Self {
            automation,
            walker,
            elements: RefCell::new(HashMap::new()),
        })
    }

    fn element(&self, node: &ObjectRef) -> Option<IUIAutomationElement> {
        if let Some(hwnd) = parse_hwnd(&node.path) {
            // SAFETY: a COM call with a window handle; a dead handle fails.
            return unsafe { self.automation.ElementFromHandle(hwnd) }.ok();
        }
        self.elements.borrow().get(&node.path).cloned()
    }

    fn children(&self, bus: &str, element: &IUIAutomationElement) -> Vec<Option<ObjectRef>> {
        let mut out = Vec::new();
        // SAFETY: COM calls on elements this module holds.
        let mut child = unsafe { self.walker.GetFirstChildElement(element) }.ok();
        while let Some(current) = child {
            let path = runtime_path(&current);
            out.push(path.map(|path| {
                self.elements
                    .borrow_mut()
                    .insert(path.clone(), current.clone());
                ObjectRef::new(bus, path)
            }));
            if out.len() >= 2_000 {
                break;
            }
            // SAFETY: as above.
            child = unsafe { self.walker.GetNextSiblingElement(&current) }.ok();
        }
        out
    }

    fn application(&self, node: &ObjectRef) -> Option<NodeInfo> {
        let pid = parse_pid(&node.bus)?;
        let windows: Vec<Option<ObjectRef>> = top_windows()
            .into_iter()
            .filter(|window| window.pid == pid)
            .map(|window| Some(ObjectRef::new(&node.bus, hwnd_path(window.hwnd))))
            .collect();
        if windows.is_empty() {
            return None;
        }
        let mut states = States::default();
        states.insert(bit("showing"));
        Some(NodeInfo {
            role: "application".to_owned(),
            name: process::name(pid).unwrap_or_default(),
            states,
            actions: Vec::new(),
            editable_text: false,
            children: windows,
        })
    }

    fn describe(&self, node: &ObjectRef) -> Option<NodeInfo> {
        if node.path == "/" {
            return self.application(node);
        }
        let element = self.element(node)?;
        // SAFETY: getters on an element this module holds; a failure reads
        // as an empty field, the way a failed AT-SPI read does.
        let info = unsafe {
            let control = element.CurrentControlType().unwrap_or_default();
            let password = element.CurrentIsPassword().is_ok_and(BOOL::as_bool);
            let window = parse_hwnd(&node.path);
            let role = if window.is_some() {
                "frame"
            } else {
                role_of(control, password)
            };
            let name = match window {
                Some(hwnd) => window_title(hwnd),
                None => element
                    .CurrentName()
                    .map(|name| name.to_string())
                    .unwrap_or_default(),
            };
            let mut states = States::default();
            if element.CurrentIsEnabled().is_ok_and(BOOL::as_bool) {
                states.insert(bit("enabled"));
                states.insert(bit("sensitive"));
            }
            if !element.CurrentIsOffscreen().is_ok_and(BOOL::as_bool) {
                states.insert(bit("showing"));
                states.insert(bit("visible"));
            }
            if element
                .CurrentIsKeyboardFocusable()
                .is_ok_and(BOOL::as_bool)
            {
                states.insert(bit("focusable"));
            }
            if element.CurrentHasKeyboardFocus().is_ok_and(BOOL::as_bool) {
                states.insert(bit("focused"));
            }
            if window.is_some_and(|hwnd| GetForegroundWindow() == hwnd) {
                states.insert(bit("active"));
            }
            let mut actions = Vec::new();
            if element
                .GetCurrentPatternAs::<IUIAutomationInvokePattern>(UIA_InvokePatternId)
                .is_ok()
            {
                actions.push("click".to_owned());
            }
            if let Ok(toggle) =
                element.GetCurrentPatternAs::<IUIAutomationTogglePattern>(UIA_TogglePatternId)
            {
                actions.push("toggle".to_owned());
                if toggle
                    .CurrentToggleState()
                    .is_ok_and(|state| state == ToggleState_On)
                {
                    states.insert(bit("checked"));
                }
            }
            if let Ok(selection) = element.GetCurrentPatternAs::<IUIAutomationSelectionItemPattern>(
                UIA_SelectionItemPatternId,
            ) {
                actions.push("select".to_owned());
                if selection.CurrentIsSelected().is_ok_and(BOOL::as_bool) {
                    states.insert(bit("selected"));
                }
            }
            if let Ok(expand) = element.GetCurrentPatternAs::<IUIAutomationExpandCollapsePattern>(
                UIA_ExpandCollapsePatternId,
            ) {
                actions.push("expand or collapse".to_owned());
                if expand.CurrentExpandCollapseState().is_ok_and(|state| {
                    state == ExpandCollapseState_Expanded
                        || state == ExpandCollapseState_PartiallyExpanded
                }) {
                    states.insert(bit("expanded"));
                }
            }
            let writable = window.is_none()
                && element
                    .GetCurrentPatternAs::<IUIAutomationValuePattern>(UIA_ValuePatternId)
                    .is_ok_and(|value| !value.CurrentIsReadOnly().is_ok_and(BOOL::as_bool));
            if writable {
                states.insert(bit("editable"));
            }
            NodeInfo {
                role: role.to_owned(),
                name,
                states,
                actions,
                editable_text: writable,
                children: Vec::new(),
            }
        };
        Some(NodeInfo {
            children: self.children(&node.bus, &element),
            ..info
        })
    }

    fn value(&self, node: &ObjectRef) -> Result<IUIAutomationValuePattern, CallError> {
        let element = self
            .element(node)
            .ok_or_else(|| CallError::Lost("the element is gone".to_owned()))?;
        // SAFETY: a COM call on an element this module holds.
        unsafe { element.GetCurrentPatternAs::<IUIAutomationValuePattern>(UIA_ValuePatternId) }
            .map_err(|error| call_error(&error))
    }
}

/// Text as the walk compares it: the line breaks a write sent, whatever
/// the control stores (RichEdit keeps `\r`).
fn unify_breaks(text: &str) -> String {
    text.replace("\r\n", "\n").replace('\r', "\n")
}

impl Tree for Uia {
    fn applications(&self) -> Result<Vec<ObjectRef>, AccessibilityError> {
        let mut seen = Vec::new();
        for window in top_windows() {
            if !seen.contains(&window.pid) {
                seen.push(window.pid);
            }
        }
        Ok(seen
            .into_iter()
            .map(|pid| ObjectRef::new(bus_of(pid), "/"))
            .collect())
    }

    fn read(&self, nodes: &[ObjectRef]) -> Vec<Option<NodeInfo>> {
        nodes.iter().map(|node| self.describe(node)).collect()
    }

    fn pid(&self, bus: &str) -> u32 {
        parse_pid(bus).unwrap_or(0)
    }

    fn interfaces(&self, node: &ObjectRef) -> Result<Vec<String>, CallError> {
        let element = self
            .element(node)
            .ok_or_else(|| CallError::Lost("the element is gone".to_owned()))?;
        let mut out = Vec::new();
        // SAFETY: COM calls on an element this module holds.
        unsafe {
            if let Ok(value) =
                element.GetCurrentPatternAs::<IUIAutomationValuePattern>(UIA_ValuePatternId)
            {
                if !value.CurrentIsReadOnly().is_ok_and(BOOL::as_bool) {
                    out.push(EDITABLE_TEXT.to_owned());
                }
                out.push(TEXT.to_owned());
            } else if element
                .GetCurrentPatternAs::<IUIAutomationTextPattern>(UIA_TextPatternId)
                .is_ok()
            {
                out.push(TEXT.to_owned());
            }
        }
        Ok(out)
    }

    fn do_action(&self, node: &ObjectRef, index: i32) -> Result<bool, CallError> {
        let element = self
            .element(node)
            .ok_or_else(|| CallError::Lost("the element is gone".to_owned()))?;
        let info = self
            .describe(node)
            .ok_or_else(|| CallError::Lost("the element is gone".to_owned()))?;
        let action = usize::try_from(index)
            .ok()
            .and_then(|index| info.actions.get(index))
            .ok_or_else(|| CallError::Refused("no such action".to_owned()))?;
        // SAFETY: COM calls on an element this module holds; each pattern
        // call is one UIA transaction with the timeout set at connect.
        let done = unsafe {
            match action.as_str() {
                "click" => element
                    .GetCurrentPatternAs::<IUIAutomationInvokePattern>(UIA_InvokePatternId)
                    .and_then(|pattern| pattern.Invoke()),
                "toggle" => element
                    .GetCurrentPatternAs::<IUIAutomationTogglePattern>(UIA_TogglePatternId)
                    .and_then(|pattern| pattern.Toggle()),
                "select" => element
                    .GetCurrentPatternAs::<IUIAutomationSelectionItemPattern>(
                        UIA_SelectionItemPatternId,
                    )
                    .and_then(|pattern| pattern.Select()),
                "expand or collapse" => element
                    .GetCurrentPatternAs::<IUIAutomationExpandCollapsePattern>(
                        UIA_ExpandCollapsePatternId,
                    )
                    .and_then(|pattern| {
                        if pattern
                            .CurrentExpandCollapseState()
                            .is_ok_and(|state| state == ExpandCollapseState_Collapsed)
                        {
                            pattern.Expand()
                        } else {
                            pattern.Collapse()
                        }
                    }),
                _ => return Err(CallError::Refused("no such action".to_owned())),
            }
        };
        match done {
            Ok(()) => Ok(true),
            // A disabled element refuses the pattern call: not performed.
            Err(error) if error.code().0 == UIA_E_TIMEOUT => Err(CallError::Timeout),
            Err(_) if !info.states.contains(bit("enabled")) => Ok(false),
            Err(error) => Err(call_error(&error)),
        }
    }

    fn set_text_contents(&self, node: &ObjectRef, text: &str) -> Result<bool, CallError> {
        let value = self.value(node)?;
        // SAFETY: a COM call with a string this frame owns.
        unsafe { value.SetValue(&BSTR::from(text)) }
            .map(|()| true)
            .map_err(|error| call_error(&error))
    }

    fn character_count(&self, node: &ObjectRef) -> Result<i32, CallError> {
        self.text(node)
            .map(|text| i32::try_from(text.chars().count()).unwrap_or(i32::MAX))
    }

    fn text(&self, node: &ObjectRef) -> Result<String, CallError> {
        if let Ok(value) = self.value(node) {
            // SAFETY: a getter on a pattern this frame holds.
            return unsafe { value.CurrentValue() }
                .map(|text| unify_breaks(&text.to_string()))
                .map_err(|error| call_error(&error));
        }
        let element = self
            .element(node)
            .ok_or_else(|| CallError::Lost("the element is gone".to_owned()))?;
        // SAFETY: COM calls on an element this module holds.
        unsafe {
            element
                .GetCurrentPatternAs::<IUIAutomationTextPattern>(UIA_TextPatternId)
                .and_then(|pattern| pattern.DocumentRange())
                .and_then(|range| range.GetText(-1))
        }
        .map(|text| unify_breaks(&text.to_string()))
        .map_err(|error| call_error(&error))
    }
}

/// The frame of a window in canvas coordinates: the visible bounds DWM
/// draws (without the invisible resize border), moved so the virtual
/// screen starts at 0,0 as the canvas does.
#[must_use]
pub fn window_frame(hwnd: HWND) -> Option<(i32, i32, i32, i32)> {
    use windows::Win32::Graphics::Dwm::DWMWA_EXTENDED_FRAME_BOUNDS;
    use windows::Win32::UI::WindowsAndMessaging::{
        GetSystemMetrics, SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN,
    };
    let mut rect = RECT::default();
    // SAFETY: a RECT out-parameter of the size given.
    unsafe {
        DwmGetWindowAttribute(
            hwnd,
            DWMWA_EXTENDED_FRAME_BOUNDS,
            (&raw mut rect).cast(),
            std::mem::size_of::<RECT>() as u32,
        )
    }
    .ok()?;
    // SAFETY: plain metric reads.
    let (left, top) = unsafe {
        (
            GetSystemMetrics(SM_XVIRTUALSCREEN),
            GetSystemMetrics(SM_YVIRTUALSCREEN),
        )
    };
    Some((
        rect.left - left,
        rect.top - top,
        rect.right - rect.left,
        rect.bottom - rect.top,
    ))
}
