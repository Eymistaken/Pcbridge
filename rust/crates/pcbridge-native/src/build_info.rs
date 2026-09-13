//! What this binary says about itself: version, build id, protocol, target.
//!
//! Printed by `--build-info` and `--version`, and the build id travels in the
//! `initialize` response. Reading any of it opens nothing -- no session bus,
//! no PipeWire, no state directory -- so `doctor.sh` can describe an install
//! without asking for a permission or starting a screen share.

use pcbridge_core::{PROTOCOL_MAJOR, PROTOCOL_MINOR};
use serde_json::{Value, json};

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
pub const TARGET: &str = env!("PCBRIDGE_TARGET");
pub const PROFILE: &str = env!("PCBRIDGE_PROFILE");

/// The commit the binary was built from, with `-dirty` for uncommitted Rust
/// changes, when `scripts/build-native.sh` built it; `dev` otherwise.
#[must_use]
pub fn build_id() -> &'static str {
    match option_env!("PCBRIDGE_BUILD_ID") {
        Some(id) if !id.is_empty() => id,
        _ => "dev",
    }
}

/// One JSON object; the shape `doctor.sh` and the packaging test read.
///
/// `test_harness` is here because a harness build answers the protocol with a
/// deterministic fake backend. It looks healthy and reads no screen, and one
/// was nearly used for a live capture test by mistake (2026-09-13).
#[must_use]
pub fn build_info() -> Value {
    json!({
        "name": "pcbridge-native",
        "version": VERSION,
        "build_id": build_id(),
        "protocol": {"major": PROTOCOL_MAJOR, "minor": PROTOCOL_MINOR},
        "target": TARGET,
        "profile": PROFILE,
        "test_harness": cfg!(feature = "test-harness"),
    })
}

#[must_use]
pub fn version_line() -> String {
    format!(
        "pcbridge-native {VERSION} (build {}, protocol {PROTOCOL_MAJOR}.{PROTOCOL_MINOR}, \
         {TARGET}, {PROFILE})",
        build_id()
    )
}
