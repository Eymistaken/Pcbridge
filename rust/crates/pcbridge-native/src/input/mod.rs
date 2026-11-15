//! Keyboard, pointer and relative-pointer behavior shared by every system.
//!
//! What is here never touches a device: combos and their 30 ms pause, held
//! keys and buttons with the 120 s auto-release, fail-closed recovery, smooth
//! paths and the 60 ms click hold. Each system supplies the devices that
//! write the events (`platform::linux::input` through uinput,
//! `platform::windows::input` through `SendInput`).

mod keyboard;
mod keys;
mod pointer;
mod relative;
// The Windows dispatcher takes these up with its input methods.
#[cfg(target_os = "linux")]
pub(crate) mod requests;

pub use keyboard::*;
pub use keys::{Key, supported_key_names, supported_keys};
pub use pointer::*;
pub use relative::*;
