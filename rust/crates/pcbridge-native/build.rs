//! Build metadata the helper reports about itself.
//!
//! Cargo tells build scripts the target triple and the profile; ordinary code
//! only sees the architecture and OS. `PCBRIDGE_BUILD_ID` is set by
//! `scripts/build-native.sh`; any other build reports `dev`.

#![forbid(unsafe_code)]

fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    println!("cargo:rerun-if-env-changed=PCBRIDGE_BUILD_ID");
    let target = std::env::var("TARGET").unwrap_or_else(|_| "unknown".to_owned());
    let profile = std::env::var("PROFILE").unwrap_or_else(|_| "unknown".to_owned());
    println!("cargo:rustc-env=PCBRIDGE_TARGET={target}");
    println!("cargo:rustc-env=PCBRIDGE_PROFILE={profile}");
}
