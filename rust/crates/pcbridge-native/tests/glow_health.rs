//! Python/native parity for exact, fresh presentation evidence.

use pcbridge_core::LeaseToken;
use pcbridge_native::platform::linux::glow_state::FrameRecord;
use serde_json::Value;

#[test]
fn frame_records_match_the_shared_refusal_fixture() {
    let data: Value = serde_json::from_str(include_str!(
        "../../../../tests/fixtures/native/glow_health_cases.json"
    ))
    .unwrap();
    let token = LeaseToken::new(
        data["token"]["grant_id"].as_str().unwrap(),
        data["token"]["revoke_epoch"].as_u64().unwrap(),
    );
    for case in data["cases"].as_array().unwrap() {
        let mut raw = data["base"].clone();
        raw.as_object_mut()
            .unwrap()
            .extend(case["patch"].as_object().unwrap().clone());
        let valid = serde_json::from_value::<FrameRecord>(raw).is_ok_and(|record| {
            record.matches(
                &token,
                data["display"].as_str().unwrap(),
                data["signature"].as_str().unwrap(),
                case["now_ms"].as_u64().unwrap(),
            )
        });
        assert_eq!(
            valid,
            case["expected"].as_bool().unwrap(),
            "{}",
            case["name"]
        );
    }
}

#[test]
fn a_delayed_old_observer_cannot_overwrite_replacement_health() {
    use pcbridge_core::{LEASE_LOCK_FILE, LEASE_STATE_FILE};
    use std::fs::{File, OpenOptions};
    use std::io::Write;
    use std::os::unix::fs::OpenOptionsExt;
    use std::time::{Duration, SystemTime, UNIX_EPOCH};

    let directory = std::env::temp_dir().join(format!(
        "pcbridge-glow-write-{}-{}",
        std::process::id(),
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    std::fs::create_dir(&directory).unwrap();
    let data: Value = serde_json::from_str(include_str!(
        "../../../../tests/fixtures/native/glow_health_cases.json"
    ))
    .unwrap();
    let old: FrameRecord = serde_json::from_value(data["base"].clone()).unwrap();
    let epoch = old.revoke_epoch;
    let lease = |grant_id: &str| {
        serde_json::json!({
            "schema_version": 1, "grant_id": grant_id, "revoke_epoch": epoch,
            "until": 0.0, "hard_until": 0.0,
        })
    };
    std::fs::write(
        directory.join(LEASE_STATE_FILE),
        lease(&old.grant_id).to_string(),
    )
    .unwrap();
    old.write(&directory).unwrap();
    // An expired identity may publish unavailable health, never authorization.
    assert!(
        pcbridge_core::DesktopLease::read(&directory.join(LEASE_STATE_FILE))
            .unwrap()
            .native_token_at(1.0)
            .is_none()
    );
    let lock = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .open(directory.join(LEASE_LOCK_FILE))
        .unwrap();
    lock.lock().unwrap();
    let (sent, received) = std::sync::mpsc::channel();
    let waiting_directory = directory.clone();
    let writer = std::thread::spawn(move || {
        let result = old.write(&waiting_directory);
        sent.send(result).unwrap();
    });
    assert!(received.recv_timeout(Duration::from_millis(100)).is_err());
    std::fs::write(
        directory.join(LEASE_STATE_FILE),
        lease("replacement").to_string(),
    )
    .unwrap();
    let mut health = File::create(directory.join("glow.json")).unwrap();
    health.write_all(b"replacement health").unwrap();
    lock.unlock().unwrap();
    received
        .recv_timeout(Duration::from_secs(2))
        .unwrap()
        .unwrap();
    writer.join().unwrap();
    assert_eq!(
        std::fs::read(directory.join("glow.json")).unwrap(),
        b"replacement health"
    );
    std::fs::remove_dir_all(directory).unwrap();
}
