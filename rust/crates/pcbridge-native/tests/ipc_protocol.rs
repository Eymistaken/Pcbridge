use std::io::{Read, Write};
use std::process::{Command, Output, Stdio};

use serde_json::{Value, json};

const MAX_HEADER_BYTES: u32 = 64 * 1024;
const MAX_BINARY_BYTES: u64 = 128 * 1024 * 1024;

fn request(id: &str, method: &str, params: Value) -> Value {
    json!({
        "protocol": {"major": 1, "minor": 0},
        "id": id,
        "method": method,
        "params": params,
        "binary_len": 0,
    })
}

fn initialize(id: &str) -> Value {
    request(
        id,
        "initialize",
        json!({
            "client_version": "ipc-contract-test",
            "supported_minor": [0],
            "state_dir": "/tmp/pcbridge-state-fixture",
            "runtime_dir": "/tmp/pcbridge-runtime-fixture",
        }),
    )
}

fn encode_header(header: &Value) -> Vec<u8> {
    let json = serde_json::to_vec(header).expect("test header should serialize");
    let mut frame = Vec::with_capacity(4 + json.len());
    frame.extend_from_slice(&(json.len() as u32).to_be_bytes());
    frame.extend_from_slice(&json);
    frame
}

fn command() -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_pcbridge-native"));
    #[cfg(feature = "test-harness")]
    command.arg("--test-mode");
    command
        .env_remove("DBUS_SESSION_BUS_ADDRESS")
        .env_remove("WAYLAND_DISPLAY")
        .env_remove("DISPLAY")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    command
}

fn run_input(input: &[u8], chunk_size: usize) -> Output {
    let mut child = command().spawn().expect("native harness should start");
    let mut stdin = child.stdin.take().expect("stdin should be piped");
    for chunk in input.chunks(chunk_size) {
        stdin
            .write_all(chunk)
            .expect("test input should be written");
        stdin.flush().expect("test input should flush");
    }
    drop(stdin);
    child
        .wait_with_output()
        .expect("native harness should terminate")
}

fn decode_frames(bytes: &[u8]) -> Vec<Value> {
    let mut cursor = std::io::Cursor::new(bytes);
    let mut frames = Vec::new();

    while (cursor.position() as usize) < bytes.len() {
        let mut prefix = [0_u8; 4];
        cursor
            .read_exact(&mut prefix)
            .expect("stdout must contain only complete framed responses");
        let header_len = u32::from_be_bytes(prefix) as usize;
        assert!(header_len <= MAX_HEADER_BYTES as usize);

        let mut header = vec![0_u8; header_len];
        cursor
            .read_exact(&mut header)
            .expect("response header should be complete");
        let value: Value =
            serde_json::from_slice(&header).expect("response header should be valid JSON");
        let binary_len = value["binary_len"]
            .as_u64()
            .expect("response should declare binary_len");
        assert!(binary_len <= MAX_BINARY_BYTES);

        let mut binary = vec![0_u8; binary_len as usize];
        cursor
            .read_exact(&mut binary)
            .expect("response binary payload should be complete");
        assert!(
            binary.is_empty(),
            "Task 2.1 control responses have no binary payload"
        );
        frames.push(value);
    }

    frames
}

fn framed_sequence(headers: &[Value]) -> Vec<u8> {
    headers.iter().flat_map(encode_header).collect()
}

#[test]
fn clean_eof_exits_without_output() {
    let output = run_input(&[], 1);
    assert!(output.status.success());
    assert!(output.stdout.is_empty());
}

#[test]
fn partial_reads_reassemble_complete_frames() {
    let input = framed_sequence(&[
        initialize("partial:1"),
        request("partial:2", "shutdown", json!({})),
    ]);
    let output = run_input(&input, 1);

    assert!(
        output.status.success(),
        "stderr: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    let frames = decode_frames(&output.stdout);
    assert_eq!(frames.len(), 2);
    assert_eq!(frames[0]["id"], "partial:1");
    assert_eq!(frames[1]["id"], "partial:2");
}

#[test]
fn oversized_header_is_rejected_before_allocation() {
    let output = run_input(&(MAX_HEADER_BYTES + 1).to_be_bytes(), 4);
    assert_eq!(output.status.code(), Some(2));
    assert!(output.stdout.is_empty());
}

#[test]
fn oversized_binary_is_rejected_before_payload_read() {
    let mut header = initialize("oversized:1");
    header["binary_len"] = Value::from(MAX_BINARY_BYTES + 1);
    let output = run_input(&encode_header(&header), usize::MAX);

    assert_eq!(output.status.code(), Some(2));
    assert!(output.stdout.is_empty());
}

#[test]
fn malformed_json_exits_cleanly_without_stdout_noise() {
    let mut input = (1_u32).to_be_bytes().to_vec();
    input.push(b'{');
    let output = run_input(&input, usize::MAX);

    assert_eq!(output.status.code(), Some(2));
    assert!(output.stdout.is_empty());
    assert!(!String::from_utf8_lossy(&output.stderr).contains("panicked"));
}

#[test]
fn missing_binary_length_is_a_protocol_error() {
    let mut header = initialize("missing-length:1");
    header
        .as_object_mut()
        .expect("fixture header should be an object")
        .remove("binary_len");
    let output = run_input(&encode_header(&header), usize::MAX);

    assert_eq!(output.status.code(), Some(2));
    assert!(output.stdout.is_empty());
}

#[test]
fn invalid_request_values_are_not_written_to_diagnostics() {
    let marker = "private-request-marker";
    let mut header = initialize("invalid:1");
    header["id"] = json!({"secret": marker});
    let output = run_input(&encode_header(&header), usize::MAX);

    assert_eq!(output.status.code(), Some(2));
    assert!(output.stdout.is_empty());
    assert!(!String::from_utf8_lossy(&output.stderr).contains(marker));
}

#[test]
fn truncated_binary_payload_exits_cleanly() {
    let mut header = initialize("truncated:1");
    header["binary_len"] = Value::from(2);
    let mut input = encode_header(&header);
    input.push(0);
    let output = run_input(&input, usize::MAX);

    assert_eq!(output.status.code(), Some(2));
    assert!(output.stdout.is_empty());
}

#[test]
fn initialize_is_required_before_other_methods() {
    let input = framed_sequence(&[
        request("handshake:1", "ping", json!({})),
        initialize("handshake:2"),
        request("handshake:3", "shutdown", json!({})),
    ]);
    let output = run_input(&input, usize::MAX);

    assert!(output.status.success());
    let frames = decode_frames(&output.stdout);
    assert_eq!(frames.len(), 3);
    assert_eq!(frames[0]["error"]["code"], "NOT_INITIALIZED");
    assert!(frames[1].get("result").is_some());
    assert_eq!(frames[2]["result"]["shutdown"], true);
}

#[test]
fn unknown_method_returns_structured_error_and_connection_stays_usable() {
    let input = framed_sequence(&[
        initialize("unknown:1"),
        request("unknown:2", "desktop.not-implemented", json!({})),
        request("unknown:3", "shutdown", json!({})),
    ]);
    let output = run_input(&input, usize::MAX);

    assert!(output.status.success());
    let frames = decode_frames(&output.stdout);
    assert_eq!(frames.len(), 3);
    assert_eq!(frames[1]["id"], "unknown:2");
    assert_eq!(frames[1]["error"]["code"], "UNKNOWN_METHOD");
    assert_eq!(frames[2]["id"], "unknown:3");
}

#[test]
fn unknown_major_returns_error_then_closes_connection() {
    let mut header = initialize("version:1");
    header["protocol"]["major"] = Value::from(2);
    let input = framed_sequence(&[header, request("version:2", "ping", json!({}))]);
    let output = run_input(&input, usize::MAX);

    assert!(output.status.success());
    let frames = decode_frames(&output.stdout);
    assert_eq!(frames.len(), 1);
    assert_eq!(frames[0]["id"], "version:1");
    assert_eq!(frames[0]["error"]["code"], "UNSUPPORTED_PROTOCOL");
}

#[test]
fn pipelined_request_ids_remain_distinct() {
    let input = framed_sequence(&[
        initialize("parallel:1"),
        request("parallel:2", "ping", json!({})),
        request("parallel:3", "capabilities", json!({})),
        request(
            "parallel:4",
            "cancel",
            json!({"target_id": "parallel:pending"}),
        ),
        request("parallel:5", "shutdown", json!({})),
    ]);
    let output = run_input(&input, usize::MAX);

    assert!(output.status.success());
    let frames = decode_frames(&output.stdout);
    let ids: Vec<_> = frames
        .iter()
        .map(|frame| frame["id"].as_str().unwrap())
        .collect();
    assert_eq!(
        ids,
        [
            "parallel:1",
            "parallel:2",
            "parallel:3",
            "parallel:4",
            "parallel:5"
        ]
    );
}

#[cfg(feature = "test-harness")]
#[test]
fn test_mode_responses_are_deterministic() {
    let input = framed_sequence(&[
        initialize("deterministic:1"),
        request("deterministic:2", "capabilities", json!({})),
        request("deterministic:3", "shutdown", json!({})),
    ]);

    let first = run_input(&input, usize::MAX);
    let second = run_input(&input, usize::MAX);
    assert!(first.status.success());
    assert!(second.status.success());
    assert_eq!(first.stdout, second.stdout);

    let frames = decode_frames(&first.stdout);
    assert_eq!(frames[0]["result"]["instance_id"], "test-native-instance");
    assert_eq!(frames[1]["result"]["backend"], "test.fake");
}
