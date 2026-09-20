//! Relative pointer -- the SECOND, separate uinput device (Adim 7).
//!
//! WHY A SEPARATE DEVICE
//!     An application that locks the pointer hides the cursor, pins it, and
//!     reads relative motion (`zwp_relative_pointer_v1`). It never sees the
//!     absolute "go to this point" the other device sends, so games, 3D and
//!     CAD viewports and WebGL canvases could be clicked but not turned.
//!     `REL_X`/`REL_Y` were NOT added to the absolute device: its <=1 px
//!     accuracy across two monitors was measured, and any change to how udev
//!     classifies it invalidates that measurement (the `BTN_TOUCH` lesson).
//!
//! BUTTONS ARE DECLARED AND NEVER EMITTED -- MEASURED 2026-09-20
//!     A `REL_X`/`REL_Y` device created without `EV_KEY` gets no
//!     `ID_INPUT_MOUSE` from udev and moved the cursor zero pixels, while its
//!     button-carrying twin moved 23 for the same call. So the three buttons
//!     exist for that classification only. This type exposes `move_by` and
//!     nothing else, and its event type cannot express a key at all: emitting
//!     a button from here is impossible by construction, not by convention.
//!
//! NO HELD STATE, NO TIMER
//!     The hold timer on the absolute pointer exists because a button can be
//!     left down. Nothing here can be left down, so there is no `held` set,
//!     no deadline and no worker thread to join.

use std::fmt;
use std::io;
use std::sync::{Mutex, MutexGuard};
use std::time::Duration;

use evdev::uinput::VirtualDevice;
use evdev::{AttributeSet, BusType, EventType, InputEvent, InputId, KeyCode, RelativeAxisCode};

use pcbridge_core::input::relative_chunks;

use super::pointer::{PointerClock, PointerError, SystemPointerClock};
use crate::lifecycle::{FailClosed, LifecycleFailure};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RelativeAxis {
    X,
    Y,
}

impl RelativeAxis {
    #[must_use]
    pub const fn code_name(self) -> &'static str {
        match self {
            Self::X => "REL_X",
            Self::Y => "REL_Y",
        }
    }
}

/// One relative motion event. There is deliberately no key variant.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct RelativeEvent {
    axis: RelativeAxis,
    value: i32,
}

impl RelativeEvent {
    #[must_use]
    pub const fn new(axis: RelativeAxis, value: i32) -> Self {
        Self { axis, value }
    }

    #[must_use]
    pub const fn axis(self) -> RelativeAxis {
        self.axis
    }

    #[must_use]
    pub const fn code_name(self) -> &'static str {
        self.axis.code_name()
    }

    #[must_use]
    pub const fn value(self) -> i32 {
        self.value
    }
}

pub trait RelativeDevice: Send + 'static {
    fn emit(&mut self, events: &[RelativeEvent]) -> io::Result<()>;
}

pub trait RelativeService: FailClosed + fmt::Debug {
    /// Nudge by `(dx, dy)`; returns the delta actually sent after clamping.
    fn move_by(&self, dx: i32, dy: i32) -> Result<(i32, i32), PointerError>;
    fn is_closed(&self) -> bool;
    fn close(&self);
}

#[must_use]
pub fn supported_relative_pointer_axes() -> Vec<String> {
    ["REL_X", "REL_Y"].into_iter().map(str::to_owned).collect()
}

#[must_use]
pub fn supported_relative_pointer_buttons() -> Vec<String> {
    ["BTN_LEFT", "BTN_RIGHT", "BTN_MIDDLE"]
        .into_iter()
        .map(str::to_owned)
        .collect()
}

pub struct EvdevRelativeDevice {
    device: VirtualDevice,
}

impl fmt::Debug for EvdevRelativeDevice {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("EvdevRelativeDevice(..)")
    }
}

impl EvdevRelativeDevice {
    pub fn create() -> io::Result<Self> {
        let keys: AttributeSet<KeyCode> =
            [KeyCode::BTN_LEFT, KeyCode::BTN_RIGHT, KeyCode::BTN_MIDDLE]
                .into_iter()
                .collect();
        let axes: AttributeSet<RelativeAxisCode> =
            [RelativeAxisCode::REL_X, RelativeAxisCode::REL_Y]
                .into_iter()
                .collect();
        let device = VirtualDevice::builder()?
            .name("pcbridge-pointer-rel")
            .input_id(InputId::new(BusType::BUS_VIRTUAL, 0, 0, 1))
            .with_keys(&keys)?
            .with_relative_axes(&axes)?
            .build()?;
        Ok(Self { device })
    }
}

impl RelativeDevice for EvdevRelativeDevice {
    fn emit(&mut self, events: &[RelativeEvent]) -> io::Result<()> {
        let events: Vec<InputEvent> = events
            .iter()
            .map(|event| {
                let code = match event.axis {
                    RelativeAxis::X => RelativeAxisCode::REL_X.0,
                    RelativeAxis::Y => RelativeAxisCode::REL_Y.0,
                };
                InputEvent::new(EventType::RELATIVE.0, code, event.value)
            })
            .collect();
        self.device.emit(&events)
    }
}

struct RelativeState<D> {
    device: Option<D>,
    closed: bool,
}

pub struct Relative<D: RelativeDevice, C: PointerClock> {
    state: Mutex<RelativeState<D>>,
    clock: C,
    step: Duration,
}

impl<D: RelativeDevice, C: PointerClock> fmt::Debug for Relative<D, C> {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("Relative")
            .field("closed", &self.is_closed())
            .finish_non_exhaustive()
    }
}

impl<D: RelativeDevice, C: PointerClock> Relative<D, C> {
    #[must_use]
    pub fn new(device: D, clock: C, step: Duration) -> Self {
        Self {
            state: Mutex::new(RelativeState {
                device: Some(device),
                closed: false,
            }),
            clock,
            step,
        }
    }

    /// Send `(dx, dy)` as chunks roughly `step` apart.
    ///
    /// The lock is taken PER CHUNK, exactly as `Pointer::scroll` does, so a
    /// revoke landing mid-nudge stops the rest instead of waiting it out.
    pub fn move_by(&self, dx: i32, dy: i32) -> Result<(i32, i32), PointerError> {
        let chunks = relative_chunks(dx, dy);
        if chunks.is_empty() {
            return Ok((0, 0));
        }
        let total = chunks
            .iter()
            .fold((0, 0), |sum, chunk| (sum.0 + chunk.0, sum.1 + chunk.1));
        let last = chunks.len() - 1;
        for (index, (step_x, step_y)) in chunks.into_iter().enumerate() {
            {
                let mut state = self.state();
                if state.closed || state.device.is_none() {
                    return Err(PointerError::Closed);
                }
                let mut events = Vec::with_capacity(2);
                if step_x != 0 {
                    events.push(RelativeEvent::new(RelativeAxis::X, step_x));
                }
                if step_y != 0 {
                    events.push(RelativeEvent::new(RelativeAxis::Y, step_y));
                }
                emit_or_close(&mut state, &events)?;
            }
            if index < last {
                self.clock.sleep(self.step);
            }
        }
        Ok(total)
    }

    #[must_use]
    pub fn is_closed(&self) -> bool {
        self.state().closed
    }

    pub fn close(&self) {
        let mut state = self.state();
        state.closed = true;
        state.device.take();
    }

    fn state(&self) -> MutexGuard<'_, RelativeState<D>> {
        self.state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }
}

impl<D: RelativeDevice, C: PointerClock> FailClosed for Relative<D, C> {
    fn close_fail_closed(&self, _reason: LifecycleFailure) {
        self.close();
    }
}

impl<D: RelativeDevice, C: PointerClock> RelativeService for Relative<D, C> {
    fn move_by(&self, dx: i32, dy: i32) -> Result<(i32, i32), PointerError> {
        Self::move_by(self, dx, dy)
    }

    fn is_closed(&self) -> bool {
        Self::is_closed(self)
    }

    fn close(&self) {
        Self::close(self);
    }
}

impl<D: RelativeDevice, C: PointerClock> Drop for Relative<D, C> {
    fn drop(&mut self) {
        self.close();
    }
}

/// Write, or give the device up.
///
/// Its own dozen lines rather than a generalised `emit_or_close`: there are no
/// buttons to release here, and widening the absolute device's fail-closed
/// path to serve two state shapes is how that path grows a bug.
fn emit_or_close<D: RelativeDevice>(
    state: &mut RelativeState<D>,
    events: &[RelativeEvent],
) -> Result<(), PointerError> {
    let emitted = state
        .device
        .as_mut()
        .ok_or(PointerError::Closed)?
        .emit(events);
    if let Err(error) = emitted {
        state.closed = true;
        state.device.take();
        return Err(PointerError::Device(error));
    }
    Ok(())
}

pub type NativeRelativePointer = Relative<EvdevRelativeDevice, SystemPointerClock>;
