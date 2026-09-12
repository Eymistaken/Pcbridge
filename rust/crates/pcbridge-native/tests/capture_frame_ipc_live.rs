//! The production `capture.frame` path, driven over the real stdio protocol.
//!
//! Skipped unless `PCBRIDGE_TEST_CAPTURE=1`: it briefly opens a screen share
//! and reads pixels. No image is written and no input is sent.
//!
//! This covers the one seam nothing else does. `ipc_protocol.rs` drives the
//! deterministic fake backend, and `capture_frame_live.rs` calls the library
//! directly -- neither exercises grant matching, the `mutter:` scheme, the
//! snapshot lookup or the `NativeCapture` wiring as a client would reach them.
//! Those were verified by hand once; this keeps them verified.

use std::fs;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{Value, json};

static NEXT_FIXTURE: AtomicU64 = AtomicU64::new(0);

const GRANT_ID: &str = "capture-ipc-live";
const REVOKE_EPOCH: u64 = 11;

fn enabled() -> bool {
    std::env::var("PCBRIDGE_TEST_CAPTURE").as_deref() == Ok("1")
}

fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock should follow the Unix epoch")
        .as_secs_f64()
}

fn fixture_root() -> PathBuf {
    let root = std::env::temp_dir().join(format!(
        "pcbridge-capture-ipc-{}-{}",
        std::process::id(),
        NEXT_FIXTURE.fetch_add(1, Ordering::Relaxed)
    ));
    fs::create_dir_all(root.join("state")).expect("state dir");
    fs::create_dir_all(root.join("runtime")).expect("runtime dir");
    root
}

/// A grant in a throwaway directory: the user's real desktop grant is never
/// read or written by this test.
fn write_grant(state: &Path) {
    let moment = now();
    let temporary = state.join("desktop_unlock.json.tmp");
    fs::write(
        &temporary,
        serde_json::to_vec(&json!({
            "schema_version": 1,
            "grant_id": GRANT_ID,
            "revoke_epoch": REVOKE_EPOCH,
            "until": moment + 120.0,
            "hard_until": moment + 120.0,
            "reason": "capture ipc contract",
            "granted": moment,
            "granted_by": "desktop_unlock",
        }))
        .expect("grant json"),
    )
    .expect("write grant");
    fs::rename(temporary, state.join("desktop_unlock.json")).expect("publish grant");
}

struct Helper {
    child: Child,
    stdin: ChildStdin,
    stdout: ChildStdout,
}

impl Helper {
    /// Spawned with the graphical session environment intact -- unlike the
    /// protocol tests, this one has to reach Mutter and PipeWire.
    fn start(state: &Path, runtime: &Path) -> Self {
        let mut child = Command::new(env!("CARGO_BIN_EXE_pcbridge-native"))
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .expect("native helper should start");
        let stdin = child.stdin.take().expect("stdin");
        let stdout = child.stdout.take().expect("stdout");
        let mut helper = Self {
            child,
            stdin,
            stdout,
        };
        let result = helper.call(
            "initialize",
            json!({
                "client_version": "capture-ipc-live",
                "supported_minor": [0],
                "state_dir": state.to_str().expect("utf-8 state dir"),
                "runtime_dir": runtime.to_str().expect("utf-8 runtime dir"),
            }),
        );
        assert!(
            result.0["lease_bound"] == json!(true),
            "the helper should bind the fixture grant: {:?}",
            result.0
        );
        helper
    }

    /// Send one request, read one response. Returns the header and the binary
    /// payload that followed it.
    fn call(&mut self, method: &str, params: Value) -> (Value, Vec<u8>) {
        static NEXT_ID: AtomicU64 = AtomicU64::new(0);
        let id = format!("live:{}", NEXT_ID.fetch_add(1, Ordering::Relaxed));
        let header = json!({
            "protocol": {"major": 1, "minor": 0},
            "id": id,
            "method": method,
            "params": params,
            "binary_len": 0,
        });
        let json = serde_json::to_vec(&header).expect("request json");
        self.stdin
            .write_all(&(json.len() as u32).to_be_bytes())
            .expect("write length");
        self.stdin.write_all(&json).expect("write header");
        self.stdin.flush().expect("flush");

        let mut prefix = [0_u8; 4];
        self.stdout
            .read_exact(&mut prefix)
            .expect("response length");
        let mut response = vec![0; u32::from_be_bytes(prefix) as usize];
        self.stdout
            .read_exact(&mut response)
            .expect("response header");
        let response: Value = serde_json::from_slice(&response).expect("response json");
        assert_eq!(response["id"], json!(id), "responses must match by id");

        let binary_len = response["binary_len"].as_u64().expect("binary_len");
        let mut binary = vec![0; binary_len as usize];
        self.stdout.read_exact(&mut binary).expect("binary payload");
        let body = response["result"].clone();
        let body = if body.is_null() {
            response["error"].clone()
        } else {
            body
        };
        (body, binary)
    }

    fn shutdown(mut self) -> String {
        let _ = self.call("shutdown", json!({}));
        drop(self.stdin);
        let mut stderr = String::new();
        if let Some(mut pipe) = self.child.stderr.take() {
            let _ = pipe.read_to_string(&mut stderr);
        }
        let status = self.child.wait().expect("helper should exit");
        assert!(status.success(), "helper exited with {status}");
        stderr
    }
}

fn capture_params(display_id: &str, topology: &str) -> Value {
    json!({
        "display_id": display_id,
        "topology_id": topology,
        "session_id": "capture-ipc-live-session",
        "grant_id": GRANT_ID,
        "revoke_epoch": REVOKE_EPOCH,
        "timeout_ms": 5000,
        "freshness": "after_request",
        "include_pointer": false,
    })
}

#[test]
fn a_client_receives_a_real_png_and_every_refusal_is_typed() {
    if !enabled() {
        eprintln!("skipped: set PCBRIDGE_TEST_CAPTURE=1 to capture over the real protocol");
        return;
    }

    let root = fixture_root();
    let state = root.join("state");
    write_grant(&state);
    let mut helper = Helper::start(&state, &root.join("runtime"));

    let (snapshot, _) = helper.call("display.snapshot", json!({}));
    let topology = snapshot["topology_id"]
        .as_str()
        .expect("topology")
        .to_owned();
    let monitor = &snapshot["monitors"][0];
    let connector = monitor["connector"].as_str().expect("connector").to_owned();
    let width = monitor["width"].as_u64().expect("width");
    let height = monitor["height"].as_u64().expect("height");
    let display_id = format!("mutter:{connector}");

    // ---------------------------------------------------------- the real frame
    let (result, png) = helper.call("capture.frame", capture_params(&display_id, &topology));
    assert!(
        result["code"].is_null(),
        "capture should have succeeded: {result:?}"
    );
    assert_eq!(result["mime_type"], json!("image/png"));
    assert_eq!(result["backend"], json!("linux.mutter.pipewire"));
    assert_eq!(result["pixel_size"], json!([width, height]));
    assert_eq!(
        result["desktop_rect"],
        json!([monitor["x"], monitor["y"], width, height])
    );
    assert_eq!(result["include_pointer"], json!(false));
    assert!(
        !result["frame_identity_source"]
            .as_str()
            .expect("identity source")
            .is_empty(),
        "a frame identity must always say where it came from"
    );
    assert!(
        png.starts_with(b"\x89PNG\r\n\x1a\n"),
        "the binary payload is not a PNG"
    );

    let decoder = png::Decoder::new(std::io::Cursor::new(&png));
    let mut reader = decoder.read_info().expect("PNG header");
    let mut pixels = vec![0; reader.output_buffer_size().expect("output size")];
    let info = reader.next_frame(&mut pixels).expect("PNG frame");
    pixels.truncate(info.buffer_size());
    assert_eq!(
        (u64::from(info.width), u64::from(info.height)),
        (width, height)
    );
    assert!(
        pixels.chunks_exact(4).any(|pixel| pixel[..3] != [0, 0, 0]),
        "the delivered PNG decoded to an all-black placeholder"
    );
    assert!(
        pixels.chunks_exact(4).all(|pixel| pixel[3] == 0xFF),
        "the delivered PNG has transparent pixels"
    );

    // ------------------------------------------------------------- the refusals
    let mut wrong_grant = capture_params(&display_id, &topology);
    wrong_grant["grant_id"] = json!("some-other-grant");
    let (error, binary) = helper.call("capture.frame", wrong_grant);
    assert_eq!(error["code"], json!("REVOKED"));
    assert_eq!(error["category"], json!("safety"));
    assert!(binary.is_empty(), "a refusal must not carry pixels");

    let mut wrong_epoch = capture_params(&display_id, &topology);
    wrong_epoch["revoke_epoch"] = json!(REVOKE_EPOCH + 1);
    let (error, _) = helper.call("capture.frame", wrong_epoch);
    assert_eq!(error["code"], json!("REVOKED"));

    let (error, _) = helper.call(
        "capture.frame",
        capture_params(&display_id, "v1|0,0,800,600,1.0000,0,1"),
    );
    assert_eq!(error["code"], json!("DISPLAY_CHANGED"));

    let (error, _) = helper.call(
        "capture.frame",
        capture_params("mutter:DP-NOT-HERE", &topology),
    );
    assert_eq!(error["code"], json!("DISPLAY_MAPPING_UNKNOWN"));

    let (error, _) = helper.call("capture.frame", capture_params(&connector, &topology));
    assert_eq!(
        error["code"],
        json!("INVALID_PARAMS"),
        "a display_id without a scheme must not resolve"
    );

    let mut unsupported_freshness = capture_params(&display_id, &topology);
    unsupported_freshness["freshness"] = json!("newest");
    let (error, _) = helper.call("capture.frame", unsupported_freshness);
    assert_eq!(error["code"], json!("INVALID_PARAMS"));

    // A refusal must leave the helper usable.
    let (result, png) = helper.call("capture.frame", capture_params(&display_id, &topology));
    assert!(
        result["code"].is_null(),
        "capture after a refusal: {result:?}"
    );
    assert!(png.starts_with(b"\x89PNG\r\n\x1a\n"));

    let stderr = helper.shutdown();
    assert!(stderr.is_empty(), "helper wrote to stderr: {stderr}");
    let _ = fs::remove_dir_all(&root);
}
