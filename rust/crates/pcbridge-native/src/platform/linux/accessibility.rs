//! AT-SPI on Linux: the bus the shared accessibility walk
//! (`crate::accessibility`) reads. Everything else is re-exported from
//! there, so existing paths still resolve.

pub mod bus;

pub use crate::accessibility::*;
