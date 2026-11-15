//! The Linux input devices: uinput through the `evdev` crate.
//!
//! The `evdev` crate owns every uinput ioctl. These devices only write the
//! events that the shared services in `crate::input` decide on; the
//! behavior above the device (combos, held keys, timers, smooth paths) is
//! there, not here.

use std::fmt;
use std::io;

use evdev::uinput::VirtualDevice;
use evdev::{
    AbsInfo, AbsoluteAxisCode, AttributeSet, BusType, EventType, InputEvent, InputId, KeyCode,
    RelativeAxisCode, UinputAbsSetup,
};

pub use crate::input::*;

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
            .map(|event| InputEvent::new(EventType::KEY.0, event.key().code(), event.value()))
            .collect();
        self.device.emit(&events)
    }
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
                let (kind, code) = match (event.event_type_name(), event.code_name()) {
                    ("EV_KEY", "BTN_LEFT") => (EventType::KEY.0, KeyCode::BTN_LEFT.code()),
                    ("EV_KEY", "BTN_RIGHT") => (EventType::KEY.0, KeyCode::BTN_RIGHT.code()),
                    ("EV_KEY", "BTN_MIDDLE") => (EventType::KEY.0, KeyCode::BTN_MIDDLE.code()),
                    ("EV_ABS", "ABS_X") => (EventType::ABSOLUTE.0, AbsoluteAxisCode::ABS_X.0),
                    ("EV_ABS", "ABS_Y") => (EventType::ABSOLUTE.0, AbsoluteAxisCode::ABS_Y.0),
                    ("EV_REL", "REL_WHEEL") => {
                        (EventType::RELATIVE.0, RelativeAxisCode::REL_WHEEL.0)
                    }
                    ("EV_REL", "REL_HWHEEL") => {
                        (EventType::RELATIVE.0, RelativeAxisCode::REL_HWHEEL.0)
                    }
                    _ => unreachable!("pointer events are constructed from fixed codes"),
                };
                InputEvent::new(kind, code, event.value())
            })
            .collect();
        self.device.emit(&events)
    }
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
                let code = match event.axis() {
                    RelativeAxis::X => RelativeAxisCode::REL_X.0,
                    RelativeAxis::Y => RelativeAxisCode::REL_Y.0,
                };
                InputEvent::new(EventType::RELATIVE.0, code, event.value())
            })
            .collect();
        self.device.emit(&events)
    }
}

/// The uinput code of every key the name table reaches, in code order.
#[must_use]
pub fn supported_key_codes() -> Vec<KeyCode> {
    supported_keys()
        .into_iter()
        .map(|key| KeyCode::new(key.code()))
        .collect()
}

pub type NativeKeyboard = Keyboard<EvdevKeyboardDevice, SystemClock>;
pub type NativePointer = Pointer<EvdevPointerDevice, SystemPointerClock>;
pub type NativeRelativePointer = Relative<EvdevRelativeDevice, SystemPointerClock>;

#[cfg(test)]
mod tests {
    use evdev::KeyCode;

    use super::Key;

    #[test]
    fn every_key_maps_to_the_uinput_code_of_the_same_name() {
        for key in Key::ALL {
            assert_eq!(format!("{:?}", KeyCode::new(key.code())), key.name());
        }
    }
}
