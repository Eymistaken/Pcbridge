use std::fs;
use std::time::{SystemTime, UNIX_EPOCH};

use pcbridge_core::{DesktopLease, LeaseToken};
use serde_json::json;

fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock should follow the Unix epoch")
        .as_secs_f64()
}

fn fixture_path(name: &str) -> std::path::PathBuf {
    let root = std::env::temp_dir().join(format!(
        "pcbridge-lease-contract-{}-{name}",
        std::process::id()
    ));
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&root).expect("fixture directory should be created");
    root.join("desktop_unlock.json")
}

#[test]
fn fresh_grant_produces_an_exact_native_token() {
    let path = fixture_path("fresh");
    let moment = now();
    fs::write(
        &path,
        serde_json::to_vec(&json!({
            "schema_version": 1,
            "grant_id": "grant-a",
            "revoke_epoch": 7,
            "until": moment + 60.0,
            "hard_until": moment + 300.0,
            "reason": "contract",
            "granted": moment,
            "granted_by": "desktop_unlock"
        }))
        .unwrap(),
    )
    .unwrap();

    let lease = DesktopLease::read(&path).expect("fresh lease should parse");
    let token = lease
        .native_token_at(moment)
        .expect("fresh active grant should bind native work");

    assert_eq!(token, LeaseToken::new("grant-a", 7));
    assert!(lease.validates_at(&token, moment));
    assert!(!lease.validates_at(&LeaseToken::new("grant-a", 8), moment));
}

#[test]
fn legacy_or_expired_grants_fail_closed_for_native_work() {
    let path = fixture_path("legacy");
    let moment = now();
    fs::write(
        &path,
        serde_json::to_vec(&json!({"until": moment + 60.0})).unwrap(),
    )
    .unwrap();
    let legacy = DesktopLease::read(&path).unwrap();
    assert!(legacy.is_active_at(moment));
    assert!(legacy.native_token_at(moment).is_none());

    fs::write(
        &path,
        serde_json::to_vec(&json!({
            "schema_version": 1,
            "grant_id": "expired",
            "revoke_epoch": 2,
            "until": moment - 1.0,
            "hard_until": moment + 60.0
        }))
        .unwrap(),
    )
    .unwrap();
    let expired = DesktopLease::read(&path).unwrap();
    assert!(expired.native_token_at(moment).is_none());
}

#[test]
fn missing_or_malformed_state_is_not_a_grant() {
    let path = fixture_path("invalid");
    assert!(DesktopLease::read(&path).is_err());
    fs::write(&path, b"{").unwrap();
    assert!(DesktopLease::read(&path).is_err());
}
