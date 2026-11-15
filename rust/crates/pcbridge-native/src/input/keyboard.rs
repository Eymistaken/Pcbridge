//! The keyboard and its held-key lifecycle, above any device.
//!
//! A device (uinput on Linux, `SendInput` on Windows) only writes events.
//! This module owns the behavior above that boundary: the key aliases, combo
//! ordering, held-key accounting, and an independent monotonic auto-release
//! timer. The same code runs on every system, so the fake-device tests pin
//! it once for all of them.

use std::collections::BTreeSet;
use std::fmt;
use std::io;
use std::sync::{Arc, Condvar, Mutex, MutexGuard};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use super::keys::{Key, canonical_name, parse_combo};
use crate::fail_closed::{FailClosed, LifecycleFailure};

const COMBO_PAUSE: Duration = Duration::from_millis(30);
/// Text is typed one character at a time, `TEXT_PAUSE` apart, with the lock
/// released in between, so a revoke stops it within one character. The
/// device decides how each character goes (Windows: the layout's keys, or a
/// Unicode packet with its own pauses; docs/dev/measured-facts.md, W6c).
const TEXT_CHUNK: usize = 1;
const TEXT_PAUSE: Duration = Duration::from_millis(10);
pub const DEFAULT_HOLD_MAX: Duration = Duration::from_secs(120);
pub const DEVICE_SETTLE: Duration = Duration::from_millis(1200);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct KeyboardEvent {
    code: Key,
    value: i32,
}

impl KeyboardEvent {
    #[must_use]
    pub const fn new(code: Key, value: i32) -> Self {
        Self { code, value }
    }

    #[must_use]
    pub const fn key(self) -> Key {
        self.code
    }

    /// The key's name in the Linux input-event spelling (`KEY_A`), which the
    /// protocol and the golden fixture use on every system.
    #[must_use]
    pub fn code_name(self) -> String {
        self.code.name().to_owned()
    }

    #[must_use]
    pub const fn value(self) -> i32 {
        self.value
    }
}

pub trait KeyboardDevice: Send + 'static {
    fn emit(&mut self, events: &[KeyboardEvent]) -> io::Result<()>;

    /// Type `text` as characters rather than keys, where the system can
    /// (Windows' `KEYEVENTF_UNICODE`). On Linux the Python side types
    /// through key names and the clipboard, and uinput has no such event.
    fn type_text(&mut self, _text: &str) -> io::Result<()> {
        Err(io::Error::new(
            io::ErrorKind::Unsupported,
            "this keyboard cannot type text as characters",
        ))
    }
}

pub trait KeyboardClock: Send + Sync + 'static {
    fn now(&self) -> Duration;
    fn sleep(&self, duration: Duration);
}

pub trait KeyboardService: FailClosed + fmt::Debug {
    fn key(&self, combo: &str) -> Result<(), KeyboardError>;
    fn key_down(&self, combo: &str) -> Result<(), KeyboardError>;
    fn key_up(&self, combo: &str) -> Result<(), KeyboardError>;
    fn type_text(&self, text: &str) -> Result<usize, KeyboardError>;
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
    #[error("text cannot be typed while a key is held")]
    HeldWhileTyping,
    #[error("the virtual keyboard failed: {0}")]
    Device(#[source] io::Error),
}

struct State<D> {
    device: Option<D>,
    held: BTreeSet<Key>,
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

    /// Type `text` as characters; returns how many were typed. Refused
    /// while a key is held, since a held modifier would turn the text into
    /// shortcuts.
    pub fn type_text(&self, text: &str) -> Result<usize, KeyboardError> {
        let characters: Vec<char> = text.chars().collect();
        let mut typed = 0;
        for (index, chunk) in characters.chunks(TEXT_CHUNK).enumerate() {
            if index > 0 {
                self.inner.clock.sleep(TEXT_PAUSE);
            }
            let piece: String = chunk.iter().collect();
            let mut state = self.state();
            ensure_open(&state)?;
            if !state.held.is_empty() {
                return Err(KeyboardError::HeldWhileTyping);
            }
            type_or_close(&mut state, &piece)?;
            typed += chunk.len();
        }
        Ok(typed)
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

    fn type_text(&self, text: &str) -> Result<usize, KeyboardError> {
        Self::type_text(self, text)
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
    possibly_pressed: &[Key],
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

/// Type one piece, or give the device up. Nothing is held (checked by the
/// caller), so there is nothing to release; a device that cannot type text
/// at all stays open.
fn type_or_close<D: KeyboardDevice>(state: &mut State<D>, text: &str) -> Result<(), KeyboardError> {
    let typed = state
        .device
        .as_mut()
        .ok_or(KeyboardError::Closed)?
        .type_text(text);
    match typed {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::Unsupported => {
            Err(KeyboardError::Device(error))
        }
        Err(error) => {
            state.deadline = None;
            state.closed = true;
            state.device.take();
            Err(KeyboardError::Device(error))
        }
    }
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
