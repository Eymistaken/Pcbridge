//! Linux absolute pointer behavior shared by the real uinput device and tests.

use std::collections::BTreeSet;
use std::fmt;
use std::fs;
use std::io;
use std::path::PathBuf;
use std::sync::{Arc, Condvar, Mutex, MutexGuard};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use evdev::uinput::VirtualDevice;
use evdev::{
    AbsInfo, AbsoluteAxisCode, AttributeSet, BusType, EventType, InputEvent, InputId, KeyCode,
    RelativeAxisCode, UinputAbsSetup,
};
use serde::{Deserialize, Serialize};

use crate::lifecycle::{FailClosed, LifecycleFailure};

const POSITION_MAX_AGE_SECONDS: f64 = 300.0;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct PointerGeometry {
    width: u32,
    height: u32,
}

impl PointerGeometry {
    pub fn new(width: u32, height: u32) -> Result<Self, PointerError> {
        if width == 0 || height == 0 || width > i32::MAX as u32 || height > i32::MAX as u32 {
            return Err(PointerError::InvalidGeometry);
        }
        Ok(Self { width, height })
    }

    #[must_use]
    pub const fn width(self) -> u32 {
        self.width
    }

    #[must_use]
    pub const fn height(self) -> u32 {
        self.height
    }

    #[must_use]
    pub const fn max_x(self) -> i32 {
        (self.width - 1) as i32
    }

    #[must_use]
    pub const fn max_y(self) -> i32 {
        (self.height - 1) as i32
    }
}

#[derive(Debug, thiserror::Error)]
pub enum PointerError {
    #[error("the pointer canvas geometry is invalid")]
    InvalidGeometry,
    #[error("the virtual pointer is closed")]
    Closed,
    #[error("the virtual pointer failed: {0}")]
    Device(#[source] io::Error),
    #[error("unknown pointer button '{0}'")]
    UnknownButton(String),
    #[error("click count must be between 1 and 3")]
    InvalidClickCount,
}

#[must_use]
pub fn supported_pointer_buttons() -> Vec<String> {
    ["BTN_LEFT", "BTN_RIGHT", "BTN_MIDDLE"]
        .into_iter()
        .map(str::to_owned)
        .collect()
}

#[must_use]
pub fn supported_pointer_relative_axes() -> Vec<String> {
    ["REL_WHEEL", "REL_HWHEEL"]
        .into_iter()
        .map(str::to_owned)
        .collect()
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PointerConfig {
    pub speed: f64,
    pub max_ms: f64,
    pub step: Duration,
    pub hold_max: Duration,
    pub drag_min_steps: usize,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum PointerEventKind {
    Key,
    Absolute,
    Relative,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct PointerEvent {
    kind: PointerEventKind,
    code: &'static str,
    value: i32,
}

impl PointerEvent {
    const fn absolute(code: &'static str, value: i32) -> Self {
        Self {
            kind: PointerEventKind::Absolute,
            code,
            value,
        }
    }

    const fn key(code: &'static str, value: i32) -> Self {
        Self {
            kind: PointerEventKind::Key,
            code,
            value,
        }
    }

    const fn relative(code: &'static str, value: i32) -> Self {
        Self {
            kind: PointerEventKind::Relative,
            code,
            value,
        }
    }

    #[must_use]
    pub const fn event_type_name(self) -> &'static str {
        match self.kind {
            PointerEventKind::Key => "EV_KEY",
            PointerEventKind::Absolute => "EV_ABS",
            PointerEventKind::Relative => "EV_REL",
        }
    }

    #[must_use]
    pub const fn code_name(self) -> &'static str {
        self.code
    }

    #[must_use]
    pub const fn value(self) -> i32 {
        self.value
    }
}

pub trait PointerDevice: Send + 'static {
    fn emit(&mut self, events: &[PointerEvent]) -> io::Result<()>;
}

pub trait PointerService: FailClosed + fmt::Debug {
    fn move_to(
        &self,
        x: i32,
        y: i32,
        smooth: Option<bool>,
        min_steps: usize,
    ) -> Result<(i32, i32), PointerError>;
    fn click(&self, button: &str, count: u8) -> Result<(), PointerError>;
    fn drag(&self, x1: i32, y1: i32, x2: i32, y2: i32, button: &str) -> Result<(), PointerError>;
    fn scroll(&self, amount: i32, horizontal: bool) -> Result<(), PointerError>;
    fn mouse_down(&self, button: &str) -> Result<(), PointerError>;
    fn mouse_up(&self, button: &str) -> Result<(), PointerError>;
    fn held(&self) -> Vec<String>;
    fn release_all(&self) -> Result<Vec<String>, PointerError>;
    fn take_auto_released(&self) -> Vec<String>;
    fn position(&self) -> Option<(i32, i32)>;
    /// Tell the pointer that something else moved the cursor: the recorded
    /// position is dropped and the next absolute move resyncs.
    fn note_external_motion(&self);
    fn is_closed(&self) -> bool;
    fn close(&self) -> Result<Vec<String>, PointerError>;
}

pub struct EvdevPointerDevice {
    device: VirtualDevice,
}

impl fmt::Debug for EvdevPointerDevice {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("EvdevPointerDevice(..)")
    }
}

impl EvdevPointerDevice {
    pub fn create(geometry: PointerGeometry) -> io::Result<Self> {
        let keys: AttributeSet<KeyCode> =
            [KeyCode::BTN_LEFT, KeyCode::BTN_RIGHT, KeyCode::BTN_MIDDLE]
                .into_iter()
                .collect();
        let relative: AttributeSet<RelativeAxisCode> =
            [RelativeAxisCode::REL_WHEEL, RelativeAxisCode::REL_HWHEEL]
                .into_iter()
                .collect();
        let x = UinputAbsSetup::new(
            AbsoluteAxisCode::ABS_X,
            AbsInfo::new(0, 0, geometry.max_x(), 0, 0, 0),
        );
        let y = UinputAbsSetup::new(
            AbsoluteAxisCode::ABS_Y,
            AbsInfo::new(0, 0, geometry.max_y(), 0, 0, 0),
        );
        let device = VirtualDevice::builder()?
            .name("pcbridge-pointer")
            .input_id(InputId::new(BusType::BUS_VIRTUAL, 0, 0, 1))
            .with_keys(&keys)?
            .with_absolute_axis(&x)?
            .with_absolute_axis(&y)?
            .with_relative_axes(&relative)?
            .build()?;
        Ok(Self { device })
    }
}

impl PointerDevice for EvdevPointerDevice {
    fn emit(&mut self, events: &[PointerEvent]) -> io::Result<()> {
        let events: Vec<_> = events
            .iter()
            .map(|event| {
                let (kind, code) = match (event.kind, event.code) {
                    (PointerEventKind::Key, "BTN_LEFT") => {
                        (EventType::KEY.0, KeyCode::BTN_LEFT.code())
                    }
                    (PointerEventKind::Key, "BTN_RIGHT") => {
                        (EventType::KEY.0, KeyCode::BTN_RIGHT.code())
                    }
                    (PointerEventKind::Key, "BTN_MIDDLE") => {
                        (EventType::KEY.0, KeyCode::BTN_MIDDLE.code())
                    }
                    (PointerEventKind::Absolute, "ABS_X") => {
                        (EventType::ABSOLUTE.0, AbsoluteAxisCode::ABS_X.0)
                    }
                    (PointerEventKind::Absolute, "ABS_Y") => {
                        (EventType::ABSOLUTE.0, AbsoluteAxisCode::ABS_Y.0)
                    }
                    (PointerEventKind::Relative, "REL_WHEEL") => {
                        (EventType::RELATIVE.0, RelativeAxisCode::REL_WHEEL.0)
                    }
                    (PointerEventKind::Relative, "REL_HWHEEL") => {
                        (EventType::RELATIVE.0, RelativeAxisCode::REL_HWHEEL.0)
                    }
                    _ => unreachable!("pointer events are constructed from fixed codes"),
                };
                InputEvent::new(kind, code, event.value)
            })
            .collect();
        self.device.emit(&events)
    }
}

pub trait PointerClock: Send + Sync + 'static {
    fn monotonic(&self) -> Duration;
    fn unix_time(&self) -> f64;
    fn sleep(&self, duration: Duration);
}

#[derive(Debug)]
pub struct SystemPointerClock(Instant);

impl Default for SystemPointerClock {
    fn default() -> Self {
        Self(Instant::now())
    }
}

impl PointerClock for SystemPointerClock {
    fn monotonic(&self) -> Duration {
        self.0.elapsed()
    }

    fn unix_time(&self) -> f64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(f64::INFINITY, |duration| duration.as_secs_f64())
    }

    fn sleep(&self, duration: Duration) {
        thread::sleep(duration);
    }
}

#[derive(Deserialize, Serialize)]
struct PersistedPosition {
    x: i32,
    y: i32,
    t: f64,
}

struct State<D> {
    device: Option<D>,
    position: Option<(i32, i32)>,
    held: BTreeSet<Button>,
    transient: BTreeSet<Button>,
    auto_released: Vec<String>,
    deadline: Option<Duration>,
    closed: bool,
    /// Something outside this device moved the cursor, so its ABS state no
    /// longer matches where the pointer is. See `note_external_motion`.
    abs_stale: bool,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
enum Button {
    Left,
    Right,
    Middle,
}

impl Button {
    const fn code(self) -> &'static str {
        match self {
            Self::Left => "BTN_LEFT",
            Self::Right => "BTN_RIGHT",
            Self::Middle => "BTN_MIDDLE",
        }
    }

    const fn name(self) -> &'static str {
        match self {
            Self::Left => "left",
            Self::Right => "right",
            Self::Middle => "middle",
        }
    }
}

struct Inner<D, C> {
    state: Mutex<State<D>>,
    wake: Condvar,
    clock: C,
    geometry: PointerGeometry,
    config: PointerConfig,
    state_file: Option<PathBuf>,
}

pub struct Pointer<D: PointerDevice, C: PointerClock> {
    inner: Arc<Inner<D, C>>,
    worker: Mutex<Option<JoinHandle<()>>>,
}

impl<D: PointerDevice, C: PointerClock> fmt::Debug for Pointer<D, C> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("Pointer")
            .field("position", &self.position())
            .field("geometry", &self.inner.geometry)
            .finish_non_exhaustive()
    }
}

impl<D: PointerDevice, C: PointerClock> Pointer<D, C> {
    #[must_use]
    pub fn new(
        device: D,
        clock: C,
        geometry: PointerGeometry,
        config: PointerConfig,
        state_file: Option<PathBuf>,
    ) -> Self {
        let position = read_position(state_file.as_ref(), clock.unix_time());
        let inner = Arc::new(Inner {
            state: Mutex::new(State {
                device: Some(device),
                position,
                held: BTreeSet::new(),
                transient: BTreeSet::new(),
                auto_released: Vec::new(),
                deadline: None,
                closed: false,
                abs_stale: false,
            }),
            wake: Condvar::new(),
            clock,
            geometry,
            config,
            state_file,
        });
        let timer_inner = Arc::clone(&inner);
        let worker = thread::Builder::new()
            .name(format!(
                "pcbridge-native-pointer-held-{}",
                std::process::id()
            ))
            .spawn(move || timer_loop(&timer_inner))
            .expect("held-button timer thread should start");
        Self {
            inner,
            worker: Mutex::new(Some(worker)),
        }
    }

    pub fn move_to(
        &self,
        x: i32,
        y: i32,
        smooth: Option<bool>,
        min_steps: usize,
    ) -> Result<(i32, i32), PointerError> {
        let target = pcbridge_core::input::global_to_device(
            x,
            y,
            self.inner.geometry.width(),
            self.inner.geometry.height(),
        )
        .ok_or(PointerError::InvalidGeometry)?;
        let wants_smooth = smooth.unwrap_or(self.inner.config.speed > 0.0);
        let start = {
            let state = self.state();
            ensure_open(&state)?;
            state.position
        };
        let path = match start {
            Some(start) if wants_smooth || min_steps > 1 => pcbridge_core::input::move_path(
                start.0,
                start.1,
                target.0,
                target.1,
                if wants_smooth {
                    self.inner.config.speed
                } else {
                    0.0
                },
                self.inner.config.max_ms,
                self.inner.config.step.as_secs_f64(),
                min_steps,
            ),
            _ => vec![target],
        };
        let mut path = path;
        if std::mem::replace(&mut self.state().abs_stale, false) {
            // A relative nudge moved the cursor without changing this
            // device's ABS state, and the kernel swallows a repeated value.
            // Step one pixel aside first, then go to the target.
            let aside = if target.0 > 0 {
                target.0 - 1
            } else {
                target.0 + 1
            };
            path.insert(0, (aside, target.1));
        }
        let last = path.len().saturating_sub(1);
        for (index, point) in path.into_iter().enumerate() {
            {
                let mut state = self.state();
                ensure_open(&state)?;
                self.emit_locked(
                    &mut state,
                    &[
                        PointerEvent::absolute("ABS_X", point.0),
                        PointerEvent::absolute("ABS_Y", point.1),
                    ],
                )?;
                state.position = Some(point);
            }
            if index < last {
                self.inner.clock.sleep(self.inner.config.step);
            }
        }
        write_position(
            self.inner.state_file.as_ref(),
            target,
            self.inner.clock.unix_time(),
        );
        Ok(target)
    }

    pub fn click(&self, button: &str, count: u8) -> Result<(), PointerError> {
        if !(1..=3).contains(&count) {
            return Err(PointerError::InvalidClickCount);
        }
        let button = parse_button(button)?;
        for index in 0..count {
            {
                let mut state = self.state();
                ensure_open(&state)?;
                self.emit_locked(&mut state, &[PointerEvent::key(button.code(), 1)])?;
                state.transient.insert(button);
            }
            self.inner.clock.sleep(Duration::from_millis(30));
            {
                let mut state = self.state();
                ensure_open(&state)?;
                self.emit_locked(&mut state, &[PointerEvent::key(button.code(), 0)])?;
                state.transient.remove(&button);
            }
            if index + 1 < count {
                self.inner.clock.sleep(Duration::from_millis(80));
            }
        }
        Ok(())
    }

    pub fn mouse_down(&self, button: &str) -> Result<(), PointerError> {
        let button = parse_button(button)?;
        let mut state = self.state();
        ensure_open(&state)?;
        self.emit_locked(&mut state, &[PointerEvent::key(button.code(), 1)])?;
        state.held.insert(button);
        arm_deadline(&self.inner, &mut state);
        self.inner.wake.notify_all();
        Ok(())
    }

    pub fn mouse_up(&self, button: &str) -> Result<(), PointerError> {
        let button = parse_button(button)?;
        let mut state = self.state();
        ensure_open(&state)?;
        self.emit_locked(&mut state, &[PointerEvent::key(button.code(), 0)])?;
        state.held.remove(&button);
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
            .map(Button::name)
            .map(str::to_owned)
            .collect()
    }

    pub fn release_all(&self) -> Result<Vec<String>, PointerError> {
        let released = release_locked(&mut self.state());
        self.inner.wake.notify_all();
        released
    }

    pub fn take_auto_released(&self) -> Vec<String> {
        std::mem::take(&mut self.state().auto_released)
    }

    pub fn scroll(&self, amount: i32, horizontal: bool) -> Result<(), PointerError> {
        let code = if horizontal {
            "REL_HWHEEL"
        } else {
            "REL_WHEEL"
        };
        let step = if amount > 0 { 1 } else { -1 };
        for _ in 0..amount.unsigned_abs().min(100) {
            {
                let mut state = self.state();
                ensure_open(&state)?;
                self.emit_locked(&mut state, &[PointerEvent::relative(code, step)])?;
            }
            self.inner.clock.sleep(Duration::from_millis(10));
        }
        Ok(())
    }

    pub fn drag(
        &self,
        x1: i32,
        y1: i32,
        x2: i32,
        y2: i32,
        button: &str,
    ) -> Result<(), PointerError> {
        self.move_to(x1, y1, None, 1)?;
        self.inner.clock.sleep(Duration::from_millis(50));
        self.mouse_down(button)?;
        self.inner.clock.sleep(Duration::from_millis(50));
        self.move_to(x2, y2, None, self.inner.config.drag_min_steps.max(1))?;
        self.inner.clock.sleep(Duration::from_millis(50));
        self.mouse_up(button)
    }

    #[must_use]
    pub fn position(&self) -> Option<(i32, i32)> {
        self.state().position
    }

    /// Something outside this device moved the cursor (the relative one).
    ///
    /// Two consequences, and both matter:
    ///
    /// 1. The recorded position is a lie. `None` already means "unknown"
    ///    everywhere in this stack -- a missing or stale file reads back as
    ///    `None` -- so the record is removed rather than marked. The next
    ///    absolute move writes a fresh one.
    /// 2. This device's ABS state did not change. MEASURED 2026-09-20: after
    ///    a relative nudge carried the cursor from 960 to 1052, sending
    ///    `ABS_X=960` from here did NOTHING -- the kernel treats a repeated
    ///    absolute value as no change. 961 worked, and 960 worked after it.
    ///    So the next absolute move has to step one pixel aside first.
    pub fn note_external_motion(&self) {
        let mut state = self.state();
        state.position = None;
        state.abs_stale = true;
        drop(state);
        if let Some(path) = self.inner.state_file.as_ref() {
            let _ = fs::remove_file(path);
        }
    }

    pub fn close(&self) -> Result<Vec<String>, PointerError> {
        let released = {
            let mut state = self.state();
            if state.closed {
                Ok(Vec::new())
            } else {
                let released = release_locked(&mut state);
                state.closed = true;
                state.device.take();
                released
            }
        };
        self.inner.wake.notify_all();
        if let Some(worker) = self.worker.lock().unwrap().take() {
            let _ = worker.join();
        }
        released
    }

    #[must_use]
    pub fn is_closed(&self) -> bool {
        self.state().closed
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

    fn emit_locked(
        &self,
        state: &mut State<D>,
        events: &[PointerEvent],
    ) -> Result<(), PointerError> {
        let result = emit_or_close(state, events);
        if result.is_err() {
            self.inner.wake.notify_all();
        }
        result
    }
}

impl<D: PointerDevice, C: PointerClock> Drop for Pointer<D, C> {
    fn drop(&mut self) {
        let _ = self.close();
    }
}

impl<D: PointerDevice, C: PointerClock> FailClosed for Pointer<D, C> {
    fn close_fail_closed(&self, _reason: LifecycleFailure) {
        let _ = self.close();
    }
}

impl<D: PointerDevice, C: PointerClock> PointerService for Pointer<D, C> {
    fn move_to(
        &self,
        x: i32,
        y: i32,
        smooth: Option<bool>,
        min_steps: usize,
    ) -> Result<(i32, i32), PointerError> {
        Self::move_to(self, x, y, smooth, min_steps)
    }

    fn click(&self, button: &str, count: u8) -> Result<(), PointerError> {
        Self::click(self, button, count)
    }

    fn drag(&self, x1: i32, y1: i32, x2: i32, y2: i32, button: &str) -> Result<(), PointerError> {
        Self::drag(self, x1, y1, x2, y2, button)
    }

    fn scroll(&self, amount: i32, horizontal: bool) -> Result<(), PointerError> {
        Self::scroll(self, amount, horizontal)
    }

    fn mouse_down(&self, button: &str) -> Result<(), PointerError> {
        Self::mouse_down(self, button)
    }

    fn mouse_up(&self, button: &str) -> Result<(), PointerError> {
        Self::mouse_up(self, button)
    }

    fn held(&self) -> Vec<String> {
        Self::held(self)
    }

    fn release_all(&self) -> Result<Vec<String>, PointerError> {
        Self::release_all(self)
    }

    fn take_auto_released(&self) -> Vec<String> {
        Self::take_auto_released(self)
    }

    fn position(&self) -> Option<(i32, i32)> {
        Self::position(self)
    }

    fn note_external_motion(&self) {
        Self::note_external_motion(self);
    }

    fn is_closed(&self) -> bool {
        Self::is_closed(self)
    }

    fn close(&self) -> Result<Vec<String>, PointerError> {
        Self::close(self)
    }
}

pub type NativePointer = Pointer<EvdevPointerDevice, SystemPointerClock>;

fn ensure_open<D>(state: &State<D>) -> Result<(), PointerError> {
    if state.closed || state.device.is_none() {
        Err(PointerError::Closed)
    } else {
        Ok(())
    }
}

fn parse_button(button: &str) -> Result<Button, PointerError> {
    match button.trim().to_ascii_lowercase().as_str() {
        "left" => Ok(Button::Left),
        "right" => Ok(Button::Right),
        "middle" => Ok(Button::Middle),
        _ => Err(PointerError::UnknownButton(button.to_owned())),
    }
}

fn arm_deadline<D, C: PointerClock>(inner: &Inner<D, C>, state: &mut State<D>) {
    state.deadline = if inner.config.hold_max.is_zero() || state.held.is_empty() {
        None
    } else {
        Some(inner.clock.monotonic() + inner.config.hold_max)
    };
}

fn timer_loop<D: PointerDevice, C: PointerClock>(inner: &Arc<Inner<D, C>>) {
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
        let now = inner.clock.monotonic();
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

fn release_locked<D: PointerDevice>(state: &mut State<D>) -> Result<Vec<String>, PointerError> {
    state.deadline = None;
    if state.held.is_empty() && state.transient.is_empty() {
        return Ok(Vec::new());
    }
    let buttons: Vec<_> = state.held.union(&state.transient).copied().collect();
    let released = buttons
        .iter()
        .copied()
        .map(Button::name)
        .map(str::to_owned)
        .collect();
    let events: Vec<_> = buttons
        .into_iter()
        .map(|button| PointerEvent::key(button.code(), 0))
        .collect();
    state.held.clear();
    state.transient.clear();
    emit_or_close(state, &events)?;
    Ok(released)
}

fn emit_or_close<D: PointerDevice>(
    state: &mut State<D>,
    events: &[PointerEvent],
) -> Result<(), PointerError> {
    let emitted = state
        .device
        .as_mut()
        .ok_or(PointerError::Closed)?
        .emit(events);
    if let Err(error) = emitted {
        let mut cleanup = state.held.clone();
        cleanup.extend(state.transient.iter().copied());
        cleanup.extend(events.iter().filter_map(|event| {
            (event.kind == PointerEventKind::Key)
                .then(|| button_from_code(event.code))
                .flatten()
        }));
        let release: Vec<_> = cleanup
            .into_iter()
            .map(|button| PointerEvent::key(button.code(), 0))
            .collect();
        if let Some(device) = state.device.as_mut() {
            let _ = device.emit(&release);
        }
        state.deadline = None;
        state.held.clear();
        state.transient.clear();
        state.closed = true;
        state.device.take();
        return Err(PointerError::Device(error));
    }
    Ok(())
}

fn button_from_code(code: &str) -> Option<Button> {
    match code {
        "BTN_LEFT" => Some(Button::Left),
        "BTN_RIGHT" => Some(Button::Right),
        "BTN_MIDDLE" => Some(Button::Middle),
        _ => None,
    }
}

fn read_position(path: Option<&PathBuf>, now: f64) -> Option<(i32, i32)> {
    let saved: PersistedPosition = serde_json::from_slice(&fs::read(path?).ok()?).ok()?;
    if now - saved.t > POSITION_MAX_AGE_SECONDS {
        return None;
    }
    Some((saved.x, saved.y))
}

fn write_position(path: Option<&PathBuf>, position: (i32, i32), now: f64) {
    let Some(path) = path else {
        return;
    };
    let saved = PersistedPosition {
        x: position.0,
        y: position.1,
        t: now,
    };
    let Ok(payload) = serde_json::to_vec(&saved) else {
        return;
    };
    if let Some(parent) = path.parent()
        && fs::create_dir_all(parent).is_err()
    {
        return;
    }
    let temporary = path.with_extension("tmp");
    if fs::write(&temporary, payload).is_ok() {
        let _ = fs::rename(temporary, path);
    }
}
