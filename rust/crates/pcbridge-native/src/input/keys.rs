//! The keys pcbridge can press, by name, independent of any device.
//!
//! `Key` numbers each key with its Linux input-event code (`KEY_A` is 30).
//! That numbering is not a Linux dependency: it descends from the PC/AT
//! scan code set 1, so for the main block it is the same number Windows
//! takes as a scan code, and it gives held keys the order they were always
//! released in (`BTreeSet` order, highest first). The uinput device passes
//! the number through; the Windows device maps it to a scan code and the
//! extended-key flag.

use super::keyboard::KeyboardError;

macro_rules! keys {
    ($($variant:ident = $code:literal, $name:literal;)*) => {
        /// One key, numbered as its Linux input-event code.
        #[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
        #[repr(u16)]
        pub enum Key {
            $($variant = $code,)*
        }

        impl Key {
            /// Every key, in code order.
            pub const ALL: &'static [Self] = &[$(Self::$variant,)*];

            /// The Linux input-event code (`KEY_A` is 30).
            #[must_use]
            pub const fn code(self) -> u16 {
                self as u16
            }

            /// The Linux input-event name (`KEY_A`), the spelling the
            /// protocol and the golden fixture use.
            #[must_use]
            pub const fn name(self) -> &'static str {
                match self {
                    $(Self::$variant => $name,)*
                }
            }
        }
    };
}

keys! {
    Esc = 1, "KEY_ESC";
    Num1 = 2, "KEY_1";
    Num2 = 3, "KEY_2";
    Num3 = 4, "KEY_3";
    Num4 = 5, "KEY_4";
    Num5 = 6, "KEY_5";
    Num6 = 7, "KEY_6";
    Num7 = 8, "KEY_7";
    Num8 = 9, "KEY_8";
    Num9 = 10, "KEY_9";
    Num0 = 11, "KEY_0";
    Minus = 12, "KEY_MINUS";
    Equal = 13, "KEY_EQUAL";
    Backspace = 14, "KEY_BACKSPACE";
    Tab = 15, "KEY_TAB";
    Q = 16, "KEY_Q";
    W = 17, "KEY_W";
    E = 18, "KEY_E";
    R = 19, "KEY_R";
    T = 20, "KEY_T";
    Y = 21, "KEY_Y";
    U = 22, "KEY_U";
    I = 23, "KEY_I";
    O = 24, "KEY_O";
    P = 25, "KEY_P";
    LeftBrace = 26, "KEY_LEFTBRACE";
    RightBrace = 27, "KEY_RIGHTBRACE";
    Enter = 28, "KEY_ENTER";
    LeftCtrl = 29, "KEY_LEFTCTRL";
    A = 30, "KEY_A";
    S = 31, "KEY_S";
    D = 32, "KEY_D";
    F = 33, "KEY_F";
    G = 34, "KEY_G";
    H = 35, "KEY_H";
    J = 36, "KEY_J";
    K = 37, "KEY_K";
    L = 38, "KEY_L";
    Semicolon = 39, "KEY_SEMICOLON";
    Apostrophe = 40, "KEY_APOSTROPHE";
    Grave = 41, "KEY_GRAVE";
    LeftShift = 42, "KEY_LEFTSHIFT";
    Backslash = 43, "KEY_BACKSLASH";
    Z = 44, "KEY_Z";
    X = 45, "KEY_X";
    C = 46, "KEY_C";
    V = 47, "KEY_V";
    B = 48, "KEY_B";
    N = 49, "KEY_N";
    M = 50, "KEY_M";
    Comma = 51, "KEY_COMMA";
    Dot = 52, "KEY_DOT";
    Slash = 53, "KEY_SLASH";
    RightShift = 54, "KEY_RIGHTSHIFT";
    LeftAlt = 56, "KEY_LEFTALT";
    Space = 57, "KEY_SPACE";
    CapsLock = 58, "KEY_CAPSLOCK";
    F1 = 59, "KEY_F1";
    F2 = 60, "KEY_F2";
    F3 = 61, "KEY_F3";
    F4 = 62, "KEY_F4";
    F5 = 63, "KEY_F5";
    F6 = 64, "KEY_F6";
    F7 = 65, "KEY_F7";
    F8 = 66, "KEY_F8";
    F9 = 67, "KEY_F9";
    F10 = 68, "KEY_F10";
    F11 = 87, "KEY_F11";
    F12 = 88, "KEY_F12";
    KpEnter = 96, "KEY_KPENTER";
    SysRq = 99, "KEY_SYSRQ";
    RightAlt = 100, "KEY_RIGHTALT";
    Home = 102, "KEY_HOME";
    Up = 103, "KEY_UP";
    PageUp = 104, "KEY_PAGEUP";
    Left = 105, "KEY_LEFT";
    Right = 106, "KEY_RIGHT";
    End = 107, "KEY_END";
    Down = 108, "KEY_DOWN";
    PageDown = 109, "KEY_PAGEDOWN";
    Insert = 110, "KEY_INSERT";
    Delete = 111, "KEY_DELETE";
    LeftMeta = 125, "KEY_LEFTMETA";
    Compose = 127, "KEY_COMPOSE";
}

const KEY_TABLE: &[(&str, Key)] = &[
    ("a", Key::A),
    ("b", Key::B),
    ("c", Key::C),
    ("d", Key::D),
    ("e", Key::E),
    ("f", Key::F),
    ("g", Key::G),
    ("h", Key::H),
    ("i", Key::I),
    ("j", Key::J),
    ("k", Key::K),
    ("l", Key::L),
    ("m", Key::M),
    ("n", Key::N),
    ("o", Key::O),
    ("p", Key::P),
    ("q", Key::Q),
    ("r", Key::R),
    ("s", Key::S),
    ("t", Key::T),
    ("u", Key::U),
    ("v", Key::V),
    ("w", Key::W),
    ("x", Key::X),
    ("y", Key::Y),
    ("z", Key::Z),
    ("0", Key::Num0),
    ("1", Key::Num1),
    ("2", Key::Num2),
    ("3", Key::Num3),
    ("4", Key::Num4),
    ("5", Key::Num5),
    ("6", Key::Num6),
    ("7", Key::Num7),
    ("8", Key::Num8),
    ("9", Key::Num9),
    ("f1", Key::F1),
    ("f2", Key::F2),
    ("f3", Key::F3),
    ("f4", Key::F4),
    ("f5", Key::F5),
    ("f6", Key::F6),
    ("f7", Key::F7),
    ("f8", Key::F8),
    ("f9", Key::F9),
    ("f10", Key::F10),
    ("f11", Key::F11),
    ("f12", Key::F12),
    ("ctrl", Key::LeftCtrl),
    ("alt", Key::LeftAlt),
    ("altgr", Key::RightAlt),
    ("shift", Key::LeftShift),
    ("rightshift", Key::RightShift),
    ("super", Key::LeftMeta),
    ("return", Key::Enter),
    ("kpenter", Key::KpEnter),
    ("escape", Key::Esc),
    ("tab", Key::Tab),
    ("space", Key::Space),
    ("backspace", Key::Backspace),
    ("delete", Key::Delete),
    ("insert", Key::Insert),
    ("home", Key::Home),
    ("end", Key::End),
    ("pageup", Key::PageUp),
    ("pagedown", Key::PageDown),
    ("up", Key::Up),
    ("down", Key::Down),
    ("left", Key::Left),
    ("right", Key::Right),
    ("capslock", Key::CapsLock),
    ("printscreen", Key::SysRq),
    ("menu", Key::Compose),
    ("minus", Key::Minus),
    ("equal", Key::Equal),
    ("comma", Key::Comma),
    ("dot", Key::Dot),
    ("slash", Key::Slash),
    ("backslash", Key::Backslash),
    ("semicolon", Key::Semicolon),
    ("apostrophe", Key::Apostrophe),
    ("grave", Key::Grave),
    ("leftbrace", Key::LeftBrace),
    ("rightbrace", Key::RightBrace),
];

pub(super) fn parse_combo(combo: &str) -> Result<Vec<Key>, KeyboardError> {
    let compact = combo.to_ascii_lowercase().replace(' ', "");
    let parts: Vec<_> = compact.split('+').filter(|part| !part.is_empty()).collect();
    if parts.is_empty() {
        return Err(KeyboardError::EmptyCombo);
    }
    parts.into_iter().map(resolve_key).collect()
}

fn resolve_key(name: &str) -> Result<Key, KeyboardError> {
    let canonical = match name {
        "control" => "ctrl",
        "meta" | "win" | "cmd" => "super",
        "rshift" | "shift_r" | "right_shift" => "rightshift",
        "enter" => "return",
        "esc" => "escape",
        "period" => "dot",
        other => other,
    };
    KEY_TABLE
        .iter()
        .find_map(|(candidate, key)| (*candidate == canonical).then_some(*key))
        .ok_or_else(|| KeyboardError::UnknownKey(name.to_owned()))
}

pub(super) fn canonical_name(key: Key) -> &'static str {
    KEY_TABLE
        .iter()
        .find_map(|(name, candidate)| (*candidate == key).then_some(*name))
        .unwrap_or("unknown")
}

/// Every key the name table reaches, in code order.
#[must_use]
pub fn supported_keys() -> Vec<Key> {
    let mut keys: Vec<_> = KEY_TABLE.iter().map(|(_, key)| *key).collect();
    keys.sort_unstable();
    keys.dedup();
    keys
}

#[must_use]
pub fn supported_key_names() -> Vec<String> {
    let mut names: Vec<_> = supported_keys()
        .into_iter()
        .map(|key| key.name().to_owned())
        .collect();
    names.sort();
    names
}

#[cfg(test)]
mod tests {
    use super::{KEY_TABLE, Key, supported_keys};

    #[test]
    fn every_key_is_in_the_name_table() {
        assert_eq!(supported_keys(), Key::ALL.to_vec());
        assert_eq!(KEY_TABLE.len(), Key::ALL.len());
    }

    #[test]
    fn keys_are_listed_in_code_order() {
        assert!(
            Key::ALL
                .windows(2)
                .all(|pair| pair[0].code() < pair[1].code())
        );
    }
}
