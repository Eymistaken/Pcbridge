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
//!   `KEYEVENTF_EXTENDEDKEY`.
//! - Text goes by the keys the foreground window's own layout uses for each
//!   character (with Shift or AltGr), and only a character that layout
//!   cannot type goes as a `KEYEVENTF_UNICODE` packet, followed by a pause.
//!   Measured in W6c: Windows 11 Notepad turned packets that arrived while
//!   it was busy (after a space, with many tabs open) into repeats of an
//!   earlier one, even 10 ms apart, while the same text by keys read back
//!   exactly at full speed.
//! - The pointer moves with `MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK`
//!   over the virtual screen, which is the canvas (`display.rs` normalizes
//!   its origin to 0,0 the way Linux does).

use std::io;

use windows::Win32::UI::Input::KeyboardAndMouse::{
    GetKeyState, GetKeyboardLayout, HKL, INPUT, INPUT_0, INPUT_KEYBOARD, INPUT_MOUSE,
    KEYBD_EVENT_FLAGS, KEYBDINPUT, KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE,
    KEYEVENTF_UNICODE, MAPVK_VK_TO_CHAR, MAPVK_VK_TO_VSC_EX, MOUSE_EVENT_FLAGS,
    MOUSEEVENTF_ABSOLUTE, MOUSEEVENTF_HWHEEL, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP,
    MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, MOUSEEVENTF_MOVE, MOUSEEVENTF_RIGHTDOWN,
    MOUSEEVENTF_RIGHTUP, MOUSEEVENTF_VIRTUALDESK, MOUSEEVENTF_WHEEL, MOUSEINPUT, MapVirtualKeyExW,
    SendInput, VIRTUAL_KEY, VK_CAPITAL, VkKeyScanExW,
};
use windows::Win32::UI::WindowsAndMessaging::{GetForegroundWindow, GetWindowThreadProcessId};

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

/// Before and after a Unicode packet: the receiver must have taken the keys
/// before it, then the packet itself, off its queue (see the module
/// comment). Measured in W6c: with a pause after the packet only, the first
/// packet after a space was lost once in three runs.
const PACKET_PAUSE: std::time::Duration = std::time::Duration::from_millis(40);

/// One character as key events: the scan codes the layout uses for it with
/// the modifiers it needs, or a Unicode packet when the layout has no key
/// for it. The flag says which.
fn character_inputs(character: char, layout: HKL, caps_lock: bool) -> (Vec<INPUT>, bool) {
    let key_press = |key: Key| {
        let (scan, extended) = scan_code(key);
        let base = if extended {
            KEYEVENTF_SCANCODE | KEYEVENTF_EXTENDEDKEY
        } else {
            KEYEVENTF_SCANCODE
        };
        vec![
            keyboard_input(scan, base),
            keyboard_input(scan, base | KEYEVENTF_KEYUP),
        ]
    };
    match character {
        '\r' | '\n' => return (key_press(Key::Enter), false),
        '\t' => return (key_press(Key::Tab), false),
        _ => {}
    }
    let mut units = [0u16; 2];
    let units = character.encode_utf16(&mut units);
    if units.len() == 1
        && let Some(inputs) = layout_inputs(units[0], character, layout, caps_lock)
    {
        return (inputs, false);
    }
    let mut inputs = Vec::with_capacity(units.len() * 2);
    for unit in units.iter() {
        inputs.push(keyboard_input(*unit, KEYEVENTF_UNICODE));
        inputs.push(keyboard_input(*unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP));
    }
    (inputs, true)
}

/// The layout's own keys for a character, or None: no key types it, it
/// needs Ctrl or Alt alone (a shortcut, not text), or it is a dead key that
/// would wait for the next character.
fn layout_inputs(unit: u16, character: char, layout: HKL, caps_lock: bool) -> Option<Vec<INPUT>> {
    // SAFETY: plain table lookups in a keyboard layout Windows loaded.
    let (found, scan, dead) = unsafe {
        let found = VkKeyScanExW(unit, layout);
        if found == -1 {
            return None;
        }
        let vk = u32::from(found as u16 & 0xff);
        let scan = MapVirtualKeyExW(vk, MAPVK_VK_TO_VSC_EX, layout);
        let dead = MapVirtualKeyExW(vk, MAPVK_VK_TO_CHAR, layout) & 0x8000_0000 != 0;
        (found, scan, dead)
    };
    let modifiers = (found as u16) >> 8;
    let (mut shift, control, alt) = (modifiers & 1 != 0, modifiers & 2 != 0, modifiers & 4 != 0);
    if dead || scan == 0 || control != alt || modifiers & !7 != 0 {
        return None;
    }
    // Caps Lock flips Shift for letters; undo that so the case comes out
    // as asked.
    if caps_lock && character.is_alphabetic() {
        shift = !shift;
    }
    let extended = scan & 0xff00 == 0xe000 || scan & 0xff00 == 0xe100;
    let key_flags = if extended {
        KEYEVENTF_SCANCODE | KEYEVENTF_EXTENDEDKEY
    } else {
        KEYEVENTF_SCANCODE
    };
    let scan = (scan & 0xff) as u16;
    let (shift_scan, _) = scan_code(Key::LeftShift);
    let (control_scan, _) = scan_code(Key::LeftCtrl);
    let (altgr_scan, _) = scan_code(Key::RightAlt);
    let altgr = control && alt;
    let mut inputs = Vec::with_capacity(8);
    if altgr {
        inputs.push(keyboard_input(control_scan, KEYEVENTF_SCANCODE));
        inputs.push(keyboard_input(
            altgr_scan,
            KEYEVENTF_SCANCODE | KEYEVENTF_EXTENDEDKEY,
        ));
    }
    if shift {
        inputs.push(keyboard_input(shift_scan, KEYEVENTF_SCANCODE));
    }
    inputs.push(keyboard_input(scan, key_flags));
    inputs.push(keyboard_input(scan, key_flags | KEYEVENTF_KEYUP));
    if shift {
        inputs.push(keyboard_input(
            shift_scan,
            KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP,
        ));
    }
    if altgr {
        inputs.push(keyboard_input(
            altgr_scan,
            KEYEVENTF_SCANCODE | KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP,
        ));
        inputs.push(keyboard_input(
            control_scan,
            KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP,
        ));
    }
    Some(inputs)
}

/// The keyboard layout of the window in front, which decides what a key
/// types there, and whether Caps Lock is on.
fn foreground_layout() -> (HKL, bool) {
    // SAFETY: plain reads; thread 0 (no foreground window) gives this
    // thread's layout.
    unsafe {
        let thread = GetWindowThreadProcessId(GetForegroundWindow(), None);
        (
            GetKeyboardLayout(thread),
            GetKeyState(i32::from(VK_CAPITAL.0)) & 1 != 0,
        )
    }
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
        let (layout, caps_lock) = foreground_layout();
        for character in text.chars() {
            let (inputs, packet) = character_inputs(character, layout, caps_lock);
            if packet {
                std::thread::sleep(PACKET_PAUSE);
            }
            send(&inputs)?;
            if packet {
                std::thread::sleep(PACKET_PAUSE);
            }
        }
        Ok(())
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
    use super::{HKL, Key, character_inputs, normalize, scan_code};

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
    fn a_character_no_layout_types_goes_as_a_unicode_packet() {
        let none = HKL::default();
        let (inputs, packet) = character_inputs('\n', none, false);
        assert_eq!(
            (inputs.len(), packet),
            (2, false),
            "a line break is the Enter key"
        );
        let (inputs, packet) = character_inputs('\u{1F600}', none, false);
        assert!(packet);
        assert_eq!(inputs.len(), 4, "a surrogate pair, down and up each");
    }
}
