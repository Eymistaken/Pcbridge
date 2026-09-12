#![forbid(unsafe_code)]

pub mod display;
pub mod error;
pub mod frame;
pub mod lease;
pub mod protocol;

pub use display::{DisplayError, DisplayState, Monitor, TOPOLOGY_VERSION};
pub use error::ProtocolError;
pub use frame::{FrameError, FrameId, FrameSpec, MAX_PIXELS, PixelFormat, RgbaFrame};
pub use lease::{DesktopLease, LEASE_SCHEMA_VERSION, LEASE_STATE_FILE, LeaseReadError, LeaseToken};
pub use protocol::{
    ErrorBody, Frame, MAX_BINARY_BYTES, MAX_HEADER_BYTES, PROTOCOL_MAJOR, PROTOCOL_MINOR,
    ProtocolVersion, RequestHeader, ResponseHeader, read_frame, write_frame,
};
