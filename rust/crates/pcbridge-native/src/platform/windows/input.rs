//! The Windows input devices: `SendInput`.
//!
//! These only write events; the shared services in `crate::input` decide
//! them (combos, held keys, timers, smooth paths, the click hold), exactly as
//! on Linux. Every event carries `INPUT_MARK` in `dwExtraInfo`: a low-level
//! hook sees it (measured in W0), so the activity guard can tell pcbridge's
//! own input from the user's.
//!
//! - Keys go by scan code (`KEYEVENTF_SCANCODE`), so a shortcut is the same
//!   physical key whatever the layout; extended keys carry
//!   `KEYEVENTF_EXTENDEDKEY`. Text goes by `KEYEVENTF_UNICODE`, which needs
//!   no layout at all.
//! - The pointer moves with `MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK`
//!   over the virtual screen, which is the canvas (`display.rs` normalizes
//!   its origin to 0,0 the way Linux does).

use std::io;

use windows::Win32::UI::Input::KeyboardAndMouse::{
    INPUT, INPUT_0, INPUT_KEYBOARD, INPUT_MOUSE, KEYBD_EVENT_FLAGS, KEYBDINPUT,
    KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE, KEYEVENTF_UNICODE,
    MOUSE_EVENT_FLAGS, MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_HWHEEL, MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP, MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, MOUSEEVENTF_MOVE,
    MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, MOUSEEVENTF_VIRTUALDESK, MOUSEEVENTF_WHEEL,
    MOUSEINPUT, SendInput, VIRTUAL_KEY,
};

use crate::input::{
    Key, KeyboardDevice, KeyboardEvent, PointerDevice, PointerEvent, PointerGeometry, RelativeAxis,
    RelativeDevice, RelativeEvent,
};

/// pcbridge's mark on every injected event (`dwExtraInfo`): "PCBR".
pub const INPUT_MARK: usize = 0x5043_4252;

/// One notch of the wheel (`WHEEL_DELTA`).
const WHEEL_DELTA: i32 = 120;

pub const BACKEND: &str = "windows.sendinput";

/// The set-1 scan code of a key and whether it is an extended (E0) key.
///
/// The Linux numbering `Key` uses descends from set 1, so the main block
/// (codes 1 to 88) is the scan code itself; the navigation block, the right
/// Alt, the Windows key, the menu key, keypad Enter and Print Screen are
/// extended keys with their own codes.
#[must_use]
pub const fn scan_code(key: Key) -> (u16, bool) {
    match key {
        Key::KpEnter => (0x1C, true),
        Key::SysRq => (0x37, true),
        Key::RightAlt => (0x38, true),
        Key::Home => (0x47, true),
        Key::Up => (0x48, true),
        Key::PageUp => (0x49, true),
        Key::Left => (0x4B, true),
        Key::Right => (0x4D, true),
        Key::End => (0x4F, true),
        Key::Down => (0x50, true),
        Key::PageDown => (0x51, true),
        Key::Insert => (0x52, true),
        Key::Delete => (0x53, true),
        Key::LeftMeta => (0x5B, true),
        Key::Compose => (0x5D, true),
        other => (other.code(), false),
    }
}

fn keyboard_input(scan: u16, flags: KEYBD_EVENT_FLAGS) -> INPUT {
    INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: VIRTUAL_KEY(0),
                wScan: scan,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: INPUT_MARK,
            },
        },
    }
}

fn mouse_input(dx: i32, dy: i32, data: i32, flags: MOUSE_EVENT_FLAGS) -> INPUT {
    INPUT {
        r#type: INPUT_MOUSE,
        Anonymous: INPUT_0 {
            mi: MOUSEINPUT {
                dx,
                dy,
                mouseData: data as _,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: INPUT_MARK,
            },
        },
    }
}

/// Send the events in one call; Windows inserts them without interleaving
/// other input. Fewer inserted than given is an error: something (UIPI, a
/// secure desktop) blocked them.
fn send(inputs: &[INPUT]) -> io::Result<()> {
    if inputs.is_empty() {
        return Ok(());
    }
    // SAFETY: a slice of initialized INPUT structures and their size, as
    // the API requires; Windows copies them before it returns.
    let sent = unsafe { SendInput(inputs, std::mem::size_of::<INPUT>() as i32) };
    if sent as usize == inputs.len() {
        Ok(())
    } else {
        Err(io::Error::other(format!(
            "SendInput inserted {sent} of {} events: {}",
            inputs.len(),
            io::Error::last_os_error()
        )))
    }
}

/// The key events for one piece of text, `KEYEVENTF_UNICODE` per UTF-16
/// unit. A line break and a tab go as the Enter and Tab keys, since many
/// applications ignore them as characters.
#[must_use]
pub fn text_inputs(text: &str) -> Vec<INPUT> {
    let mut inputs = Vec::new();
    let mut previous_cr = false;
    for character in text.chars() {
        let key = match character {
            '\n' if previous_cr => {
                previous_cr = false;
                continue;
            }
            '\r' | '\n' => Some(Key::Enter),
            '\t' => Some(Key::Tab),
            _ => None,
        };
        previous_cr = character == '\r';
        if let Some(key) = key {
            let (scan, extended) = scan_code(key);
            let base = if extended {
                KEYEVENTF_SCANCODE | KEYEVENTF_EXTENDEDKEY
            } else {
                KEYEVENTF_SCANCODE
            };
            inputs.push(keyboard_input(scan, base));
            inputs.push(keyboard_input(scan, base | KEYEVENTF_KEYUP));
            continue;
        }
        let mut units = [0u16; 2];
        for unit in character.encode_utf16(&mut units) {
            inputs.push(keyboard_input(*unit, KEYEVENTF_UNICODE));
            inputs.push(keyboard_input(*unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP));
        }
    }
    inputs
}

#[derive(Debug, Default)]
pub struct SendInputKeyboard;

impl KeyboardDevice for SendInputKeyboard {
    fn emit(&mut self, events: &[KeyboardEvent]) -> io::Result<()> {
        let inputs: Vec<INPUT> = events
            .iter()
            .map(|event| {
                let (scan, extended) = scan_code(event.key());
                let mut flags = KEYEVENTF_SCANCODE;
                if extended {
                    flags |= KEYEVENTF_EXTENDEDKEY;
                }
                if event.value() == 0 {
                    flags |= KEYEVENTF_KEYUP;
                }
                keyboard_input(scan, flags)
            })
            .collect();
        send(&inputs)
    }

    fn type_text(&mut self, text: &str) -> io::Result<()> {
        send(&text_inputs(text))
    }
}

/// The normalized absolute coordinate (0 to 65535) that lands on `pixel`
/// of a virtual screen `size` pixels wide. Windows maps a normalized value
/// `n` to the pixel `n * size / 65536`, rounding down; rounding the inverse
/// up makes every pixel exact (checked for every pixel in the tests).
#[must_use]
pub fn normalize(pixel: i32, size: u32) -> i32 {
    let size = i64::from(size.max(1));
    let pixel = i64::from(pixel).clamp(0, size - 1);
    ((pixel * 65_536 + size - 1) / size).min(65_535) as i32
}

#[derive(Debug)]
pub struct SendInputPointer {
    geometry: PointerGeometry,
    x: i32,
    y: i32,
}

impl SendInputPointer {
    #[must_use]
    pub const fn new(geometry: PointerGeometry) -> Self {
        Self {
            geometry,
            x: 0,
            y: 0,
        }
    }

    fn absolute_move(&self) -> INPUT {
        mouse_input(
            normalize(self.x, self.geometry.width()),
            normalize(self.y, self.geometry.height()),
            0,
            MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
        )
    }
}

impl PointerDevice for SendInputPointer {
    /// One uinput-style batch: absolute X and Y update the position, which
    /// is sent as one move before the next button or wheel event and at
    /// the end (where uinput's SYN would be).
    fn emit(&mut self, events: &[PointerEvent]) -> io::Result<()> {
        let mut inputs = Vec::with_capacity(events.len() + 1);
        let mut moved = false;
        for event in events {
            let flags = match (event.event_type_name(), event.code_name()) {
                ("EV_ABS", "ABS_X") => {
                    self.x = event.value();
                    moved = true;
                    continue;
                }
                ("EV_ABS", "ABS_Y") => {
                    self.y = event.value();
                    moved = true;
                    continue;
                }
                ("EV_KEY", "BTN_LEFT") if event.value() != 0 => MOUSEEVENTF_LEFTDOWN,
                ("EV_KEY", "BTN_LEFT") => MOUSEEVENTF_LEFTUP,
                ("EV_KEY", "BTN_RIGHT") if event.value() != 0 => MOUSEEVENTF_RIGHTDOWN,
                ("EV_KEY", "BTN_RIGHT") => MOUSEEVENTF_RIGHTUP,
                ("EV_KEY", "BTN_MIDDLE") if event.value() != 0 => MOUSEEVENTF_MIDDLEDOWN,
                ("EV_KEY", "BTN_MIDDLE") => MOUSEEVENTF_MIDDLEUP,
                ("EV_REL", "REL_WHEEL") => MOUSEEVENTF_WHEEL,
                ("EV_REL", "REL_HWHEEL") => MOUSEEVENTF_HWHEEL,
                _ => unreachable!("pointer events are constructed from fixed codes"),
            };
            if moved {
                inputs.push(self.absolute_move());
                moved = false;
            }
            // Linux's wheel and Windows' agree in sign: positive is up
            // (away from the user) and right.
            let data = if flags == MOUSEEVENTF_WHEEL || flags == MOUSEEVENTF_HWHEEL {
                event.value() * WHEEL_DELTA
            } else {
                0
            };
            inputs.push(mouse_input(0, 0, data, flags));
        }
        if moved {
            inputs.push(self.absolute_move());
        }
        send(&inputs)
    }
}

/// Relative motion in mickeys (`MOUSEEVENTF_MOVE` without ABSOLUTE): what a
/// game that reads raw input or clips the cursor sees as mouse motion.
#[derive(Debug, Default)]
pub struct SendInputRelative;

impl RelativeDevice for SendInputRelative {
    fn emit(&mut self, events: &[RelativeEvent]) -> io::Result<()> {
        let (mut dx, mut dy) = (0, 0);
        for event in events {
            match event.axis() {
                RelativeAxis::X => dx += event.value(),
                RelativeAxis::Y => dy += event.value(),
            }
        }
        send(&[mouse_input(dx, dy, 0, MOUSEEVENTF_MOVE)])
    }
}

#[cfg(test)]
mod tests {
    use super::{Key, normalize, scan_code, text_inputs};

    #[test]
    fn every_pixel_round_trips_through_the_normalized_coordinate() {
        for size in [800u32, 1080, 1280, 1920, 2560, 3840, 5120, 7680] {
            for pixel in 0..size as i32 {
                let n = i64::from(normalize(pixel, size));
                assert_eq!(
                    n * i64::from(size) / 65_536,
                    i64::from(pixel),
                    "{pixel}/{size}"
                );
            }
        }
    }

    #[test]
    fn the_main_block_is_its_own_scan_code() {
        assert_eq!(scan_code(Key::A), (0x1E, false));
        assert_eq!(scan_code(Key::Esc), (0x01, false));
        assert_eq!(scan_code(Key::F12), (0x58, false));
        assert_eq!(scan_code(Key::LeftCtrl), (0x1D, false));
        assert_eq!(scan_code(Key::Delete), (0x53, true));
        assert_eq!(scan_code(Key::LeftMeta), (0x5B, true));
    }

    #[test]
    fn text_becomes_unicode_units_and_line_breaks_become_enter() {
        // "a", CRLF as one Enter, a surrogate pair: 2 + 2 + 4 events.
        assert_eq!(text_inputs("a\r\n\u{1F600}").len(), 8);
        assert_eq!(text_inputs("ğ\n").len(), 4);
    }
}
