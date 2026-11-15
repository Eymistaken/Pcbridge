//! The Windows platform: Win32 calls behind safe functions.
//!
//! Every `unsafe` block in the helper lives under this module; the rest of
//! the crate denies it. Each block calls one documented Win32 function with
//! buffers this module owns for the length of the call.

pub mod capture;
pub mod clipboard;
pub mod desktop_state;
pub mod display;
pub mod input;
pub mod lifecycle;
