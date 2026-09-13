//! Linux virtual keyboard and held-key lifecycle.
//!
//! The `evdev` crate owns every uinput ioctl. This module owns the behavior
//! above that device boundary: the existing key aliases, combo ordering,
//! held-key accounting, and an independent monotonic auto-release timer.

use std::collections::BTreeSet;
use std::fmt;
use std::io;
use std::sync::{Arc, Condvar, Mutex, MutexGuard};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use evdev::uinput::VirtualDevice;
use evdev::{AttributeSet, BusType, EventType, InputEvent, InputId, KeyCode};

use crate::lifecycle::{FailClosed, LifecycleFailure};

const COMBO_PAUSE: Duration = Duration::from_millis(30);
pub const DEFAULT_HOLD_MAX: Duration = Duration::from_secs(120);
pub const DEVICE_SETTLE: Duration = Duration::from_millis(1200);

const KEY_TABLE: &[(&str, KeyCode)] = &[
    ("a", KeyCode::KEY_A),
    ("b", KeyCode::KEY_B),
    ("c", KeyCode::KEY_C),
    ("d", KeyCode::KEY_D),
    ("e", KeyCode::KEY_E),
    ("f", KeyCode::KEY_F),
    ("g", KeyCode::KEY_G),
    ("h", KeyCode::KEY_H),
    ("i", KeyCode::KEY_I),
    ("j", KeyCode::KEY_J),
    ("k", KeyCode::KEY_K),
    ("l", KeyCode::KEY_L),
    ("m", KeyCode::KEY_M),
    ("n", KeyCode::KEY_N),
    ("o", KeyCode::KEY_O),
    ("p", KeyCode::KEY_P),
    ("q", KeyCode::KEY_Q),
    ("r", KeyCode::KEY_R),
    ("s", KeyCode::KEY_S),
    ("t", KeyCode::KEY_T),
    ("u", KeyCode::KEY_U),
    ("v", KeyCode::KEY_V),
    ("w", KeyCode::KEY_W),
    ("x", KeyCode::KEY_X),
    ("y", KeyCode::KEY_Y),
    ("z", KeyCode::KEY_Z),
    ("0", KeyCode::KEY_0),
    ("1", KeyCode::KEY_1),
    ("2", KeyCode::KEY_2),
    ("3", KeyCode::KEY_3),
    ("4", KeyCode::KEY_4),
    ("5", KeyCode::KEY_5),
    ("6", KeyCode::KEY_6),
    ("7", KeyCode::KEY_7),
    ("8", KeyCode::KEY_8),
    ("9", KeyCode::KEY_9),
    ("f1", KeyCode::KEY_F1),
    ("f2", KeyCode::KEY_F2),
    ("f3", KeyCode::KEY_F3),
    ("f4", KeyCode::KEY_F4),
    ("f5", KeyCode::KEY_F5),
    ("f6", KeyCode::KEY_F6),
    ("f7", KeyCode::KEY_F7),
    ("f8", KeyCode::KEY_F8),
    ("f9", KeyCode::KEY_F9),
    ("f10", KeyCode::KEY_F10),
    ("f11", KeyCode::KEY_F11),
    ("f12", KeyCode::KEY_F12),
    ("ctrl", KeyCode::KEY_LEFTCTRL),
    ("alt", KeyCode::KEY_LEFTALT),
    ("altgr", KeyCode::KEY_RIGHTALT),
    ("shift", KeyCode::KEY_LEFTSHIFT),
    ("super", KeyCode::KEY_LEFTMETA),
    ("return", KeyCode::KEY_ENTER),
    ("kpenter", KeyCode::KEY_KPENTER),
    ("escape", KeyCode::KEY_ESC),
    ("tab", KeyCode::KEY_TAB),
    ("space", KeyCode::KEY_SPACE),
    ("backspace", KeyCode::KEY_BACKSPACE),
    ("delete", KeyCode::KEY_DELETE),
    ("insert", KeyCode::KEY_INSERT),
    ("home", KeyCode::KEY_HOME),
    ("end", KeyCode::KEY_END),
    ("pageup", KeyCode::KEY_PAGEUP),
    ("pagedown", KeyCode::KEY_PAGEDOWN),
    ("up", KeyCode::KEY_UP),
    ("down", KeyCode::KEY_DOWN),
    ("left", KeyCode::KEY_LEFT),
    ("right", KeyCode::KEY_RIGHT),
    ("capslock", KeyCode::KEY_CAPSLOCK),
    ("printscreen", KeyCode::KEY_SYSRQ),
    ("menu", KeyCode::KEY_COMPOSE),
    ("minus", KeyCode::KEY_MINUS),
    ("equal", KeyCode::KEY_EQUAL),
    ("comma", KeyCode::KEY_COMMA),
    ("dot", KeyCode::KEY_DOT),
    ("slash", KeyCode::KEY_SLASH),
    ("backslash", KeyCode::KEY_BACKSLASH),
    ("semicolon", KeyCode::KEY_SEMICOLON),
    ("apostrophe", KeyCode::KEY_APOSTROPHE),
    ("grave", KeyCode::KEY_GRAVE),
    ("leftbrace", KeyCode::KEY_LEFTBRACE),
    ("rightbrace", KeyCode::KEY_RIGHTBRACE),
];

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct KeyboardEvent {
    code: KeyCode,
    value: i32,
}

impl KeyboardEvent {
    #[must_use]
    pub const fn new(code: KeyCode, value: i32) -> Self {
        Self { code, value }
    }

    #[must_use]
    pub fn code_name(self) -> String {
        format!("{:?}", self.code)
    }

    #[must_use]
    pub const fn value(self) -> i32 {
        self.value
    }
}

pub trait KeyboardDevice: Send + 'static {
    fn emit(&mut self, events: &[KeyboardEvent]) -> io::Result<()>;
}

pub trait KeyboardClock: Send + Sync + 'static {
    fn now(&self) -> Duration;
    fn sleep(&self, duration: Duration);
}

pub trait KeyboardService: FailClosed + fmt::Debug {
    fn key(&self, combo: &str) -> Result<(), KeyboardError>;
    fn key_down(&self, combo: &str) -> Result<(), KeyboardError>;
    fn key_up(&self, combo: &str) -> Result<(), KeyboardError>;
    fn held(&self) -> Vec<String>;
    fn release_all(&self) -> Result<Vec<String>, KeyboardError>;
    fn take_auto_released(&self) -> Vec<String>;
    fn is_closed(&self) -> bool;
    fn close(&self) -> Result<Vec<String>, KeyboardError>;
}

#[derive(Debug)]
pub struct SystemClock(Instant);

impl Default for SystemClock {
    fn default() -> Self {
        Self(Instant::now())
    }
}

impl KeyboardClock for SystemClock {
    fn now(&self) -> Duration {
        self.0.elapsed()
    }

    fn sleep(&self, duration: Duration) {
        thread::sleep(duration);
    }
}

#[derive(Debug, thiserror::Error)]
pub enum KeyboardError {
    #[error("unknown key '{0}'")]
    UnknownKey(String),
    #[error("the key combination is empty")]
    EmptyCombo,
    #[error("the virtual keyboard is closed")]
    Closed,
    #[error("the virtual keyboard failed: {0}")]
    Device(#[source] io::Error),
}

pub struct EvdevKeyboardDevice {
    device: VirtualDevice,
}

impl fmt::Debug for EvdevKeyboardDevice {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("EvdevKeyboardDevice(..)")
    }
}

impl EvdevKeyboardDevice {
    pub fn create() -> io::Result<Self> {
        let keys: AttributeSet<KeyCode> = supported_key_codes().into_iter().collect();
        let device = VirtualDevice::builder()?
            .name("pcbridge-keyboard")
            .input_id(InputId::new(BusType::BUS_VIRTUAL, 0, 0, 1))
            .with_keys(&keys)?
            .build()?;
        Ok(Self { device })
    }
}

impl KeyboardDevice for EvdevKeyboardDevice {
    fn emit(&mut self, events: &[KeyboardEvent]) -> io::Result<()> {
        let events: Vec<InputEvent> = events
            .iter()
            .map(|event| InputEvent::new(EventType::KEY.0, event.code.code(), event.value))
            .collect();
        self.device.emit(&events)
    }
}

struct State<D> {
    device: Option<D>,
    held: BTreeSet<KeyCode>,
    auto_released: Vec<String>,
    deadline: Option<Duration>,
    closed: bool,
}

struct Inner<D, C> {
    state: Mutex<State<D>>,
    wake: Condvar,
    clock: C,
    hold_max: Duration,
}

pub struct Keyboard<D: KeyboardDevice, C: KeyboardClock> {
    inner: Arc<Inner<D, C>>,
    worker: Mutex<Option<JoinHandle<()>>>,
}

impl<D: KeyboardDevice, C: KeyboardClock> fmt::Debug for Keyboard<D, C> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("Keyboard")
            .field("held", &self.held())
            .finish_non_exhaustive()
    }
}

impl<D: KeyboardDevice, C: KeyboardClock> Keyboard<D, C> {
    #[must_use]
    pub fn new(device: D, clock: C, hold_max: Duration) -> Self {
        let inner = Arc::new(Inner {
            state: Mutex::new(State {
                device: Some(device),
                held: BTreeSet::new(),
                auto_released: Vec::new(),
                deadline: None,
                closed: false,
            }),
            wake: Condvar::new(),
            clock,
            hold_max,
        });
        let timer_inner = Arc::clone(&inner);
        let worker = thread::Builder::new()
            .name(format!("pcbridge-native-held-{}", std::process::id()))
            .spawn(move || timer_loop(&timer_inner))
            .expect("held-key timer thread should start");
        Self {
            inner,
            worker: Mutex::new(Some(worker)),
        }
    }

    pub fn key(&self, combo: &str) -> Result<(), KeyboardError> {
        let codes = parse_combo(combo)?;
        let mut state = self.state();
        ensure_open(&state)?;
        let pressed: Vec<_> = codes
            .iter()
            .copied()
            .map(|code| KeyboardEvent::new(code, 1))
            .collect();
        emit_or_recover(&mut state, &pressed, &codes)?;
        self.inner.clock.sleep(COMBO_PAUSE);
        let released: Vec<_> = codes
            .iter()
            .rev()
            .copied()
            .filter(|code| !state.held.contains(code))
            .map(|code| KeyboardEvent::new(code, 0))
            .collect();
        emit_or_recover(&mut state, &released, &codes)?;
        Ok(())
    }

    pub fn key_down(&self, combo: &str) -> Result<(), KeyboardError> {
        let codes = parse_combo(combo)?;
        let mut state = self.state();
        ensure_open(&state)?;
        let pressed: Vec<_> = codes
            .iter()
            .copied()
            .map(|code| KeyboardEvent::new(code, 1))
            .collect();
        emit_or_recover(&mut state, &pressed, &codes)?;
        state.held.extend(codes);
        arm_deadline(&self.inner, &mut state);
        self.inner.wake.notify_all();
        Ok(())
    }

    pub fn key_up(&self, combo: &str) -> Result<(), KeyboardError> {
        let codes = parse_combo(combo)?;
        let mut state = self.state();
        ensure_open(&state)?;
        let released: Vec<_> = codes
            .iter()
            .rev()
            .copied()
            .map(|code| KeyboardEvent::new(code, 0))
            .collect();
        emit_or_recover(&mut state, &released, &codes)?;
        for code in codes {
            state.held.remove(&code);
        }
        arm_deadline(&self.inner, &mut state);
        self.inner.wake.notify_all();
        Ok(())
    }

    #[must_use]
    pub fn held(&self) -> Vec<String> {
        self.state()
            .held
            .iter()
            .copied()
            .map(canonical_name)
            .map(str::to_owned)
            .collect()
    }

    pub fn release_all(&self) -> Result<Vec<String>, KeyboardError> {
        let mut state = self.state();
        let released = release_locked(&mut state)?;
        self.inner.wake.notify_all();
        Ok(released)
    }

    pub fn take_auto_released(&self) -> Vec<String> {
        let mut state = self.state();
        std::mem::take(&mut state.auto_released)
    }

    #[must_use]
    pub fn is_closed(&self) -> bool {
        self.state().closed
    }

    pub fn close(&self) -> Result<Vec<String>, KeyboardError> {
        let released = {
            let mut state = self.state();
            if state.closed {
                Ok(Vec::new())
            } else {
                let result = release_locked(&mut state);
                state.closed = true;
                state.device.take();
                self.inner.wake.notify_all();
                result
            }
        };
        if let Some(worker) = self.worker.lock().unwrap().take() {
            let _ = worker.join();
        }
        released
    }

    /// Wake the timer after a synthetic monotonic clock advances in tests.
    pub fn notify_clock_changed(&self) {
        self.inner.wake.notify_all();
    }

    fn state(&self) -> MutexGuard<'_, State<D>> {
        self.inner
            .state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }
}

impl<D: KeyboardDevice, C: KeyboardClock> FailClosed for Keyboard<D, C> {
    fn close_fail_closed(&self, _reason: LifecycleFailure) {
        let _ = self.close();
    }
}

impl<D: KeyboardDevice, C: KeyboardClock> KeyboardService for Keyboard<D, C> {
    fn key(&self, combo: &str) -> Result<(), KeyboardError> {
        Self::key(self, combo)
    }

    fn key_down(&self, combo: &str) -> Result<(), KeyboardError> {
        Self::key_down(self, combo)
    }

    fn key_up(&self, combo: &str) -> Result<(), KeyboardError> {
        Self::key_up(self, combo)
    }

    fn held(&self) -> Vec<String> {
        Self::held(self)
    }

    fn release_all(&self) -> Result<Vec<String>, KeyboardError> {
        Self::release_all(self)
    }

    fn take_auto_released(&self) -> Vec<String> {
        Self::take_auto_released(self)
    }

    fn is_closed(&self) -> bool {
        Self::is_closed(self)
    }

    fn close(&self) -> Result<Vec<String>, KeyboardError> {
        Self::close(self)
    }
}

impl<D: KeyboardDevice, C: KeyboardClock> Drop for Keyboard<D, C> {
    fn drop(&mut self) {
        let _ = self.close();
    }
}

fn timer_loop<D: KeyboardDevice, C: KeyboardClock>(inner: &Arc<Inner<D, C>>) {
    let mut state = inner
        .state
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    loop {
        if state.closed {
            return;
        }
        let Some(deadline) = state.deadline else {
            state = inner
                .wake
                .wait(state)
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            continue;
        };
        let now = inner.clock.now();
        if now >= deadline {
            match release_locked(&mut state) {
                Ok(released) => state.auto_released = released,
                Err(_) => state.auto_released.clear(),
            }
            continue;
        }
        let waited = inner.wake.wait_timeout(state, deadline - now);
        state = match waited {
            Ok((guard, _)) => guard,
            Err(poisoned) => poisoned.into_inner().0,
        };
    }
}

fn ensure_open<D>(state: &State<D>) -> Result<(), KeyboardError> {
    if state.closed || state.device.is_none() {
        Err(KeyboardError::Closed)
    } else {
        Ok(())
    }
}

fn arm_deadline<D, C: KeyboardClock>(inner: &Inner<D, C>, state: &mut State<D>) {
    state.deadline = if inner.hold_max.is_zero() || state.held.is_empty() {
        None
    } else {
        Some(inner.clock.now() + inner.hold_max)
    };
}

fn emit_or_recover<D: KeyboardDevice>(
    state: &mut State<D>,
    events: &[KeyboardEvent],
    possibly_pressed: &[KeyCode],
) -> Result<(), KeyboardError> {
    let emitted = state
        .device
        .as_mut()
        .ok_or(KeyboardError::Closed)?
        .emit(events);
    if let Err(error) = emitted {
        let mut cleanup = state.held.clone();
        cleanup.extend(possibly_pressed.iter().copied());
        let release: Vec<_> = cleanup
            .iter()
            .rev()
            .copied()
            .map(|code| KeyboardEvent::new(code, 0))
            .collect();
        if let Some(device) = state.device.as_mut() {
            let _ = device.emit(&release);
        }
        state.held.clear();
        state.deadline = None;
        state.closed = true;
        state.device.take();
        return Err(KeyboardError::Device(error));
    }
    Ok(())
}

fn release_locked<D: KeyboardDevice>(state: &mut State<D>) -> Result<Vec<String>, KeyboardError> {
    state.deadline = None;
    if state.held.is_empty() {
        return Ok(Vec::new());
    }
    let codes: Vec<_> = state.held.iter().rev().copied().collect();
    let names = codes
        .iter()
        .copied()
        .map(canonical_name)
        .map(str::to_owned)
        .collect();
    let events: Vec<_> = codes
        .into_iter()
        .map(|code| KeyboardEvent::new(code, 0))
        .collect();
    state.held.clear();
    if let Err(error) = state
        .device
        .as_mut()
        .ok_or(KeyboardError::Closed)?
        .emit(&events)
    {
        state.closed = true;
        state.device.take();
        return Err(KeyboardError::Device(error));
    }
    Ok(names)
}

fn parse_combo(combo: &str) -> Result<Vec<KeyCode>, KeyboardError> {
    let compact = combo.to_ascii_lowercase().replace(' ', "");
    let parts: Vec<_> = compact.split('+').filter(|part| !part.is_empty()).collect();
    if parts.is_empty() {
        return Err(KeyboardError::EmptyCombo);
    }
    parts.into_iter().map(resolve_key).collect()
}

fn resolve_key(name: &str) -> Result<KeyCode, KeyboardError> {
    let canonical = match name {
        "control" => "ctrl",
        "meta" | "win" | "cmd" => "super",
        "enter" => "return",
        "esc" => "escape",
        "period" => "dot",
        other => other,
    };
    KEY_TABLE
        .iter()
        .find_map(|(candidate, code)| (*candidate == canonical).then_some(*code))
        .ok_or_else(|| KeyboardError::UnknownKey(name.to_owned()))
}

fn canonical_name(code: KeyCode) -> &'static str {
    KEY_TABLE
        .iter()
        .find_map(|(name, candidate)| (*candidate == code).then_some(*name))
        .unwrap_or("unknown")
}

#[must_use]
pub fn supported_key_codes() -> Vec<KeyCode> {
    KEY_TABLE
        .iter()
        .map(|(_, code)| *code)
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect()
}

#[must_use]
pub fn supported_key_names() -> Vec<String> {
    let mut names: Vec<_> = supported_key_codes()
        .into_iter()
        .map(|code| format!("{code:?}"))
        .collect();
    names.sort();
    names
}

pub type NativeKeyboard = Keyboard<EvdevKeyboardDevice, SystemClock>;
