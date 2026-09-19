#![cfg(feature = "test-harness")]
//! The clipboard methods over the real protocol, in the test harness.
//!
//! The harness runs only the programs a test names in `PCBRIDGE_TEST_WL_PASTE`
//! and `PCBRIDGE_TEST_WL_COPY`, and refuses when none are named. Here they are
//! shell scripts keeping one clipboard entry in a scratch directory, so the
//! user's clipboard is never read or written.

use std::fs;
use std::io::{BufReader, Read, Write};
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::thread;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{Value, json};

static NEXT_FIXTURE: AtomicU64 = AtomicU64::new(0);

struct Fixture {
    root: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        let root = std::env::temp_dir().join(format!(
            "pcbridge-clipboard-ipc-{}-{}",
            std::process::id(),
            NEXT_FIXTURE.fetch_add(1, Ordering::Relaxed)
        ));
        for directory in ["state", "runtime", "clipboard"] {
            fs::create_dir_all(root.join(directory)).unwrap();
        }
        Self { root }
    }

    fn state(&self) -> PathBuf {
        self.root.join("state")
    }

    fn runtime(&self) -> PathBuf {
        self.root.join("runtime")
    }

    fn clipboard(&self) -> PathBuf {
        self.root.join("clipboard")
    }

    fn grant(&self, grant_id: &str, epoch: u64, seconds: f64) {
        write_grant(&self.state(), grant_id, epoch, seconds);
    }

    /// A one-entry clipboard in `clipboard/`: `mime` and `data` files.
    fn programs(&self) -> (PathBuf, PathBuf) {
        let store = format!("'{}'", self.clipboard().display());
        let paste = self.script(
            "wl-paste",
            &format!(
                "if [ \"$1\" = --list-types ]; then\n\
                   [ -f {store}/mime ] || exit 1\n\
                   cat {store}/mime; echo; exit 0\n\
                 fi\n\
                 [ \"$1\" = --type ] && [ \"$(cat {store}/mime)\" = \"$2\" ] || exit 1\n\
                 cat {store}/data\n"
            ),
        );
        let copy = self.script(
            "wl-copy",
            &format!(
                "if [ \"$1\" = --clear ]; then rm -f {store}/mime {store}/data; exit 0; fi\n\
                 [ \"$1\" = --type ] || exit 2\n\
                 printf '%s' \"$2\" > {store}/mime\n\
                 cat > {store}/data\n"
            ),
        );
        (paste, copy)
    }

    fn script(&self, name: &str, body: &str) -> PathBuf {
        let path = self.root.join(name);
        fs::write(&path, format!("#!/bin/sh\n{body}")).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
        path
    }

    fn stored(&self) -> Option<(String, Vec<u8>)> {
        let mime = fs::read_to_string(self.clipboard().join("mime")).ok()?;
        Some((mime, fs::read(self.clipboard().join("data")).unwrap()))
    }

    fn store(&self, mime: &str, data: &[u8]) {
        fs::write(self.clipboard().join("mime"), mime).unwrap();
        fs::write(self.clipboard().join("data"), data).unwrap();
    }
}

fn write_grant(state: &Path, grant_id: &str, epoch: u64, seconds: f64) {
    let moment = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs_f64();
    let temporary = state.join("desktop_unlock.json.tmp");
    fs::write(
        &temporary,
        serde_json::to_vec(&json!({
            "schema_version": 1,
            "grant_id": grant_id,
            "revoke_epoch": epoch,
            "until": moment + seconds,
            "hard_until": moment + seconds,
            "reason": "clipboard contract",
            "granted": moment,
            "granted_by": "desktop_unlock"
        }))
        .unwrap(),
    )
    .unwrap();
    fs::rename(temporary, state.join("desktop_unlock.json")).unwrap();
}

impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.root);
    }
}

struct Harness {
    child: Child,
    input: ChildStdin,
    output: BufReader<ChildStdout>,
    next_id: u64,
    handshake: Value,
}

impl Harness {
    fn start(fixture: &Fixture, programs: Option<(PathBuf, PathBuf)>) -> Self {
        let mut command = Command::new(env!("CARGO_BIN_EXE_pcbridge-native"));
        command
            .arg("--test-mode")
            .env_remove("PCBRIDGE_TEST_WL_PASTE")
            .env_remove("PCBRIDGE_TEST_WL_COPY")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        if let Some((paste, copy)) = programs {
            command
                .env("PCBRIDGE_TEST_WL_PASTE", paste)
                .env("PCBRIDGE_TEST_WL_COPY", copy);
        }
        let mut child = command.spawn().expect("native helper should start");
        let input = child.stdin.take().unwrap();
        let output = BufReader::new(child.stdout.take().unwrap());
        let mut harness = Self {
            child,
            input,
            output,
            next_id: 1,
            handshake: Value::Null,
        };
        let (response, _) = harness.request(
            "initialize",
            json!({
                "client_version": "clipboard-contract",
                "supported_minor": [0],
                "state_dir": fixture.state(),
                "runtime_dir": fixture.runtime()
            }),
            &[],
        );
        assert!(response.get("result").is_some(), "{response}");
        harness.handshake = response;
        harness
    }

    fn request(&mut self, method: &str, params: Value, binary: &[u8]) -> (Value, Vec<u8>) {
        let id = format!("clipboard:{}", self.next_id);
        self.next_id += 1;
        let header = serde_json::to_vec(&json!({
            "protocol": {"major": 1, "minor": 0},
            "id": id,
            "method": method,
            "params": params,
            "binary_len": binary.len()
        }))
        .unwrap();
        self.input
            .write_all(&(header.len() as u32).to_be_bytes())
            .unwrap();
        self.input.write_all(&header).unwrap();
        self.input.write_all(binary).unwrap();
        self.input.flush().unwrap();

        let mut prefix = [0_u8; 4];
        self.output.read_exact(&mut prefix).unwrap();
        let mut header = vec![0_u8; u32::from_be_bytes(prefix) as usize];
        self.output.read_exact(&mut header).unwrap();
        let response: Value = serde_json::from_slice(&header).unwrap();
        let length = response["binary_len"].as_u64().unwrap_or(0) as usize;
        let mut payload = vec![0_u8; length];
        self.output.read_exact(&mut payload).unwrap();
        (response, payload)
    }

    fn shutdown(mut self) {
        let (response, _) = self.request("shutdown", json!({}), &[]);
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

fn grant(id: &str, epoch: u64) -> Value {
    json!({"grant_id": id, "revoke_epoch": epoch})
}

#[test]
fn write_then_read_round_trips_type_and_bytes() {
    let fixture = Fixture::new();
    fixture.grant("clip-grant", 0, 60.0);
    let mut native = Harness::start(&fixture, Some(fixture.programs()));
    let data = "pano ğüşıöç İ\0ikili\n".as_bytes();

    let mut write = grant("clip-grant", 0);
    write["mime"] = json!("text/plain;charset=utf-8");
    let (written, _) = native.request("clipboard.write", write, data);
    assert_eq!(written["result"]["written"], data.len(), "{written}");
    assert_eq!(
        fixture.stored(),
        Some(("text/plain;charset=utf-8".to_owned(), data.to_vec()))
    );

    let (read, payload) = native.request("clipboard.read", grant("clip-grant", 0), &[]);
    assert_eq!(read["result"]["empty"], false, "{read}");
    assert_eq!(read["result"]["mime"], "text/plain;charset=utf-8");
    assert_eq!(read["binary_len"], data.len());
    assert_eq!(payload, data);

    let (cleared, _) = native.request("clipboard.clear", grant("clip-grant", 0), &[]);
    assert_eq!(cleared["result"]["cleared"], true, "{cleared}");
    let (empty, payload) = native.request("clipboard.read", grant("clip-grant", 0), &[]);
    assert_eq!(empty["result"]["empty"], true, "{empty}");
    assert_eq!(empty["result"]["mime"], Value::Null);
    assert!(payload.is_empty());
    native.shutdown();
}

#[test]
fn a_stale_grant_is_refused_before_any_program_runs() {
    let fixture = Fixture::new();
    fixture.grant("current", 3, 60.0);
    fixture.store("text/plain;charset=utf-8", b"kullanicinin panosu");
    let mut native = Harness::start(&fixture, Some(fixture.programs()));

    let mut write = grant("previous", 3);
    write["mime"] = json!("text/plain;charset=utf-8");
    for (method, params, binary) in [
        ("clipboard.read", grant("previous", 3), &b""[..]),
        ("clipboard.read", grant("current", 2), &b""[..]),
        ("clipboard.write", write, &b"yeni"[..]),
        ("clipboard.clear", grant("previous", 3), &b""[..]),
    ] {
        let (response, payload) = native.request(method, params, binary);
        assert_eq!(response["error"]["code"], "REVOKED", "{method}: {response}");
        assert_eq!(response["error"]["category"], "safety");
        assert!(
            payload.is_empty(),
            "{method}: no clipboard bytes after a refusal"
        );
    }
    assert_eq!(
        fixture.stored(),
        Some((
            "text/plain;charset=utf-8".to_owned(),
            b"kullanicinin panosu".to_vec()
        )),
        "the clipboard is untouched"
    );
    native.shutdown();
}

#[test]
fn a_revoke_while_the_clipboard_is_read_returns_no_content() {
    let fixture = Fixture::new();
    fixture.grant("clip-grant", 0, 60.0);
    fixture.store("text/plain;charset=utf-8", b"kullanicinin gizli panosu");
    let (paste, copy) = fixture.programs();
    // The content read takes 600 ms; the revoke lands 200 ms into it.
    let slow = fixture.script(
        "slow-wl-paste",
        &format!(
            "[ \"$1\" = --type ] && sleep 0.6\nexec '{}' \"$@\"\n",
            paste.display()
        ),
    );
    let mut native = Harness::start(&fixture, Some((slow, copy)));
    let state = fixture.state();
    let revoker = thread::spawn(move || {
        thread::sleep(Duration::from_millis(200));
        write_grant(&state, "clip-grant", 1, -1.0);
    });

    let (response, payload) = native.request("clipboard.read", grant("clip-grant", 0), &[]);
    revoker.join().unwrap();

    assert_eq!(response["error"]["code"], "REVOKED", "{response}");
    assert!(payload.is_empty(), "the clipboard must not follow a revoke");
    assert!(!response.to_string().contains("gizli"));
}

#[test]
fn only_clipboard_write_accepts_a_binary_payload() {
    let fixture = Fixture::new();
    fixture.grant("clip-grant", 0, 60.0);
    let mut native = Harness::start(&fixture, Some(fixture.programs()));
    for method in ["clipboard.read", "clipboard.clear", "ping"] {
        let (response, _) = native.request(method, grant("clip-grant", 0), b"bytes");
        assert_eq!(response["error"]["code"], "UNEXPECTED_BINARY", "{method}");
    }
    assert_eq!(fixture.stored(), None);
    native.shutdown();
}

#[test]
fn content_and_unknown_fields_never_travel_in_the_header() {
    let fixture = Fixture::new();
    fixture.grant("clip-grant", 0, 60.0);
    let mut native = Harness::start(&fixture, Some(fixture.programs()));

    let mut smuggled = grant("clip-grant", 0);
    smuggled["mime"] = json!("text/plain;charset=utf-8");
    smuggled["text"] = json!("gizli-icerik");
    let (response, _) = native.request("clipboard.write", smuggled, b"x");
    assert_eq!(response["error"]["code"], "INVALID_PARAMS", "{response}");
    assert!(
        !response.to_string().contains("gizli-icerik"),
        "a refused value must not be echoed"
    );

    let mut bad_mime = grant("clip-grant", 0);
    bad_mime["mime"] = json!("text/plain\n--clear");
    let (response, _) = native.request("clipboard.write", bad_mime, b"x");
    assert_eq!(response["error"]["code"], "INVALID_PARAMS", "{response}");
    assert_eq!(fixture.stored(), None, "no program ran");
    native.shutdown();
}

#[test]
fn the_harness_without_named_programs_never_reaches_a_clipboard() {
    let fixture = Fixture::new();
    fixture.grant("clip-grant", 0, 60.0);
    let mut native = Harness::start(&fixture, None);
    // Read only: if this ever regressed to the real programs, the test must
    // not be the thing that clears the user's clipboard.
    let (response, payload) = native.request("clipboard.read", grant("clip-grant", 0), &[]);
    assert_eq!(response["error"]["code"], "UNSUPPORTED", "{response}");
    assert!(payload.is_empty());
    native.shutdown();
}

#[test]
fn clipboard_is_advertised_as_a_feature() {
    let fixture = Fixture::new();
    let native = Harness::start(&fixture, None);
    let features = native.handshake["result"]["features"].as_array().unwrap();
    assert!(
        features.contains(&json!("clipboard")),
        "{}",
        native.handshake
    );
    native.shutdown();
}
