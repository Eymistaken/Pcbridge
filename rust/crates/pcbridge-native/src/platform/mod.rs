#[cfg(target_os = "linux")]
pub mod linux;
#[cfg(windows)]
#[allow(unsafe_code)]
pub mod windows;
