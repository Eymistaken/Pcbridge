#![cfg(feature = "test-harness")]

use std::fs;
use std::io::{BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde_json::{Value, json};

static NEXT_FIXTURE: AtomicU64 = AtomicU64::new(0);

fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock should follow the Unix epoch")
        .as_secs_f64()
}

fn fixture_root() -> PathBuf {
    let root = std::env::temp_dir().join(format!(
        "pcbridge-native-revoke-{}-{}",
        std::process::id(),
        NEXT_FIXTURE.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir_all(root.join("state")).unwrap();
    fs::create_dir_all(root.join("runtime")).unwrap();
    root
}

fn write_grant(state_dir: &Path, grant_id: &str, epoch: u64, seconds: f64) {
    let moment = now();
    let path = state_dir.join("desktop_unlock.json");
    let temporary = state_dir.join("desktop_unlock.json.tmp");
    fs::write(
        &temporary,
        serde_json::to_vec(&json!({
            "schema_version": 1,
            "grant_id": grant_id,
            "revoke_epoch": epoch,
            "until": moment + seconds,
            "hard_until": moment + seconds,
            "reason": "native contract",
            "granted": moment,
            "granted_by": "desktop_unlock"
        }))
        .unwrap(),
    )
    .unwrap();
    fs::rename(temporary, path).unwrap();
}

struct Harness {
    child: Child,
    input: ChildStdin,
    output: BufReader<ChildStdout>,
    next_id: u64,
}

impl Harness {
    fn start(state_dir: &Path, runtime_dir: &Path) -> Self {
        let mut child = Command::new(env!("CARGO_BIN_EXE_pcbridge-native"))
            .arg("--test-mode")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .expect("native helper should start");
        let input = child.stdin.take().unwrap();
        let output = BufReader::new(child.stdout.take().unwrap());
        let mut harness = Self {
            child,
            input,
            output,
            next_id: 1,
        };
        let response = harness.request(
            "initialize",
            json!({
                "client_version": "native-revoke-contract",
                "supported_minor": [0],
                "state_dir": state_dir,
                "runtime_dir": runtime_dir
            }),
        );
        assert!(response.get("result").is_some(), "{response}");
        harness
    }

    fn request(&mut self, method: &str, params: Value) -> Value {
        let id = format!("native-revoke:{}", self.next_id);
        self.next_id += 1;
        let header = json!({
            "protocol": {"major": 1, "minor": 0},
            "id": id,
            "method": method,
            "params": params,
            "binary_len": 0
        });
        let payload = serde_json::to_vec(&header).unwrap();
        self.input
            .write_all(&(payload.len() as u32).to_be_bytes())
            .unwrap();
        self.input.write_all(&payload).unwrap();
        self.input.flush().unwrap();

        let mut prefix = [0_u8; 4];
        self.output.read_exact(&mut prefix).unwrap();
        let mut response = vec![0_u8; u32::from_be_bytes(prefix) as usize];
        self.output.read_exact(&mut response).unwrap();
        serde_json::from_slice(&response).unwrap()
    }

    fn shutdown(mut self) {
        let response = self.request("shutdown", json!({}));
        assert_eq!(response["result"]["shutdown"], true);
        assert!(self.child.wait().unwrap().success());
    }
}

impl Drop for Harness {
    fn drop(&mut self) {
        if self.child.try_wait().ok().flatten().is_none() {
            let _ = self.child.kill();
            let _ = self.child.wait();
        }
    }
}

#[test]
fn revoke_releases_resource_and_session_never_rebinds() {
    let root = fixture_root();
    let state_dir = root.join("state");
    let runtime_dir = root.join("runtime");
    write_grant(&state_dir, "grant-a", 0, 60.0);
    let mut native = Harness::start(&state_dir, &runtime_dir);
    assert_eq!(
        native.request("test.hold_resource", json!({}))["result"]["open"],
        true
    );

    write_grant(&state_dir, "grant-a", 1, -1.0);
    let started = Instant::now();
    loop {
        let status = native.request("test.resource_status", json!({}));
        if status["result"]["open"] == false {
            break;
        }
        assert!(started.elapsed() < Duration::from_secs(1));
        thread::sleep(Duration::from_millis(20));
    }
    assert!(started.elapsed() <= Duration::from_millis(250));
    assert_eq!(
        native.request("test.hold_resource", json!({}))["error"]["code"],
        "REVOKED"
    );

    write_grant(&state_dir, "grant-b", 1, 60.0);
    assert_eq!(
        native.request("test.hold_resource", json!({}))["error"]["code"],
        "REVOKED"
    );
    native.shutdown();

    let mut fresh = Harness::start(&state_dir, &runtime_dir);
    assert_eq!(
        fresh.request("test.hold_resource", json!({}))["result"]["open"],
        true
    );
    fresh.shutdown();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn expiry_releases_an_idle_resource_within_one_second() {
    let root = fixture_root();
    let state_dir = root.join("state");
    let runtime_dir = root.join("runtime");
    write_grant(&state_dir, "short-grant", 0, 0.2);
    let mut native = Harness::start(&state_dir, &runtime_dir);
    assert_eq!(
        native.request("test.hold_resource", json!({}))["result"]["open"],
        true
    );

    let started = Instant::now();
    loop {
        let status = native.request("test.resource_status", json!({}));
        if status["result"]["open"] == false {
            break;
        }
        assert!(started.elapsed() < Duration::from_secs(1));
        thread::sleep(Duration::from_millis(20));
    }
    assert!(started.elapsed() <= Duration::from_secs(1));
    native.shutdown();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn revoke_releases_held_keyboard_keys_without_another_input_action() {
    let root = fixture_root();
    let state_dir = root.join("state");
    let runtime_dir = root.join("runtime");
    write_grant(&state_dir, "keyboard-grant", 0, 60.0);
    let mut native = Harness::start(&state_dir, &runtime_dir);

    let held = native.request(
        "input.keyboard.key_down",
        json!({
            "grant_id": "keyboard-grant",
            "revoke_epoch": 0,
            "hold_max_seconds": 120,
            "combo": "shift"
        }),
    );
    assert_eq!(held["result"]["held"], json!(["shift"]));

    write_grant(&state_dir, "keyboard-grant", 1, -1.0);
    let started = Instant::now();
    loop {
        let status = native.request("input.keyboard.held", json!({}));
        if status["result"]["held"] == json!([]) {
            break;
        }
        assert!(started.elapsed() < Duration::from_secs(1));
        thread::sleep(Duration::from_millis(20));
    }
    assert!(started.elapsed() <= Duration::from_millis(250));

    let stale = native.request(
        "input.keyboard.key",
        json!({
            "grant_id": "keyboard-grant",
            "revoke_epoch": 0,
            "hold_max_seconds": 120,
            "combo": "a"
        }),
    );
    assert_eq!(stale["error"]["code"], "REVOKED");

    native.shutdown();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn pointer_ipc_accepts_only_resolved_global_points_under_the_current_topology() {
    let root = fixture_root();
    let state_dir = root.join("state");
    let runtime_dir = root.join("runtime");
    write_grant(&state_dir, "pointer-grant", 0, 60.0);
    let mut native = Harness::start(&state_dir, &runtime_dir);
    let base = json!({
        "grant_id": "pointer-grant",
        "revoke_epoch": 0,
        "hold_max_seconds": 120,
        "topology_id": "test-layout",
        "pointer_speed": 5000,
        "pointer_max_ms": 500
    });

    let mut move_params = base.clone();
    move_params["x"] = json!(2500);
    move_params["y"] = json!(500);
    move_params["smooth"] = json!(false);
    let moved = native.request("input.pointer.move", move_params);
    assert_eq!(moved["result"]["position"], json!([2500, 500]));

    let mut stale = base.clone();
    stale["topology_id"] = json!("stale-layout");
    stale["x"] = json!(2500);
    stale["y"] = json!(500);
    assert_eq!(
        native.request("input.pointer.move", stale)["error"]["code"],
        "DISPLAY_CHANGED"
    );

    let mut unresolved = base;
    unresolved["x"] = json!(10);
    unresolved["y"] = json!(10);
    unresolved["shot"] = json!("m2-forbidden");
    assert_eq!(
        native.request("input.pointer.move", unresolved)["error"]["code"],
        "INVALID_PARAMS"
    );

    native.shutdown();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn a_new_valid_topology_rebuilds_pointer_geometry_without_erasing_fresh_state() {
    let root = fixture_root();
    let state_dir = root.join("state");
    let runtime_dir = root.join("runtime");
    write_grant(&state_dir, "pointer-topology", 0, 60.0);
    let mut native = Harness::start(&state_dir, &runtime_dir);
    let mut first = json!({
        "grant_id": "pointer-topology",
        "revoke_epoch": 0,
        "hold_max_seconds": 120,
        "topology_id": "test-layout",
        "pointer_speed": 5000,
        "pointer_max_ms": 500,
        "x": 2500,
        "y": 500,
        "smooth": false
    });
    assert_eq!(
        native.request("input.pointer.move", first.clone())["result"]["position"],
        json!([2500, 500])
    );
    let mut held = first.clone();
    held.as_object_mut().unwrap().remove("x");
    held.as_object_mut().unwrap().remove("y");
    held.as_object_mut().unwrap().remove("smooth");
    held["button"] = json!("left");
    assert_eq!(
        native.request("input.pointer.mouse_down", held)["result"]["held"],
        json!(["left"])
    );

    first["topology_id"] = json!("test-layout-wide");
    let rebuilt = native.request("input.pointer.ensure", {
        let mut params = first;
        params.as_object_mut().unwrap().remove("x");
        params.as_object_mut().unwrap().remove("y");
        params.as_object_mut().unwrap().remove("smooth");
        params
    });
    assert_eq!(rebuilt["result"]["held"], json!([]));
    assert_eq!(rebuilt["result"]["position"], json!([2500, 500]));

    native.shutdown();
    fs::remove_dir_all(root).unwrap();
}

#[test]
fn revoke_releases_held_pointer_buttons_without_another_input_action() {
    let root = fixture_root();
    let state_dir = root.join("state");
    let runtime_dir = root.join("runtime");
    write_grant(&state_dir, "pointer-revoke", 0, 60.0);
    let mut native = Harness::start(&state_dir, &runtime_dir);
    let context = json!({
        "grant_id": "pointer-revoke",
        "revoke_epoch": 0,
        "hold_max_seconds": 120,
        "topology_id": "test-layout",
        "pointer_speed": 5000,
        "pointer_max_ms": 500,
        "button": "left"
    });

    let held = native.request("input.pointer.mouse_down", context.clone());
    assert_eq!(held["result"]["held"], json!(["left"]));

    write_grant(&state_dir, "pointer-revoke", 1, -1.0);
    let started = Instant::now();
    loop {
        let status = native.request("input.pointer.held", json!({}));
        if status["result"]["held"] == json!([]) {
            break;
        }
        assert!(started.elapsed() < Duration::from_secs(1));
        thread::sleep(Duration::from_millis(20));
    }
    assert!(started.elapsed() <= Duration::from_millis(250));

    assert_eq!(
        native.request("input.pointer.mouse_down", context)["error"]["code"],
        "REVOKED"
    );
    native.shutdown();
    fs::remove_dir_all(root).unwrap();
}
