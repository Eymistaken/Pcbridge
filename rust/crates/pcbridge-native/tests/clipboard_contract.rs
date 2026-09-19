//! The native clipboard adapter against the shared fixture and real processes.
//!
//! `tests/fixtures/native/clipboard_cases.json` is the file the Python
//! contract reads: the same program calls in the same order, and the same
//! clipboard afterwards. The process tests run small shell scripts standing in
//! for `wl-paste` and `wl-copy`. The user's clipboard is never touched.

use std::cell::RefCell;
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Mutex, MutexGuard, PoisonError};
use std::time::{Duration, Instant};

use pcbridge_native::platform::linux::clipboard::{
    Clipboard, ClipboardError, Pasted, Programs, SystemPrograms, TEXT_MIME,
};
use serde_json::{Value, json};

const FIXTURE: &str = include_str!("../../../../tests/fixtures/native/clipboard_cases.json");

static NEXT_DIR: AtomicU64 = AtomicU64::new(0);

/// Tests that write a script and then run it take turns. While one thread
/// still has a new script open for writing, a fork by another thread inherits
/// that descriptor until its exec, and running the script in that window
/// fails with ETXTBSY. Seen once here as a `put` that could not start.
static PROCESS_TESTS: Mutex<()> = Mutex::new(());

fn one_at_a_time() -> MutexGuard<'static, ()> {
    PROCESS_TESTS.lock().unwrap_or_else(PoisonError::into_inner)
}

/// The compositor's clipboard as the two programs see it.
#[derive(Default)]
struct Model {
    offers: RefCell<Vec<(String, Vec<u8>, bool)>>,
    calls: RefCell<Vec<Value>>,
}

impl Model {
    fn with(initial: &Value) -> Self {
        let model = Self::default();
        for entry in initial.as_array().unwrap() {
            model.offers.borrow_mut().push((
                entry["mime"].as_str().unwrap().to_owned(),
                entry_bytes(entry),
                entry["read_fails"].as_bool().unwrap_or(false),
            ));
        }
        model
    }

    fn state(&self) -> Vec<(String, Vec<u8>)> {
        self.offers
            .borrow()
            .iter()
            .map(|(mime, data, _)| (mime.clone(), data.clone()))
            .collect()
    }

    fn record(&self, program: &str, args: &[&str]) {
        let mut call = vec![program.to_owned()];
        call.extend(args.iter().map(|arg| (*arg).to_owned()));
        self.calls.borrow_mut().push(json!(call));
    }
}

struct ModelPrograms<'a>(&'a Model);

impl Programs for ModelPrograms<'_> {
    fn paste(&self, args: &[&str], _limit: usize) -> Result<Pasted, ClipboardError> {
        self.0.record("wl-paste", args);
        let offers = self.0.offers.borrow();
        if args == ["--list-types"] {
            let listing: String = offers
                .iter()
                .map(|(mime, _, _)| format!("{mime}\n"))
                .collect();
            return Ok(Pasted {
                success: !offers.is_empty(),
                stdout: listing.into_bytes(),
            });
        }
        assert_eq!(args[0], "--type", "unexpected wl-paste call {args:?}");
        let found = offers
            .iter()
            .find(|(mime, _, fails)| mime == args[1] && !fails);
        Ok(match found {
            Some((mime, data, _)) => {
                let mut stdout = data.clone();
                // What wl-paste counts as text gets a newline unless told not to.
                let text = mime.starts_with("text/")
                    || matches!(mime.as_str(), "UTF8_STRING" | "STRING" | "TEXT");
                if text && !args.contains(&"--no-newline") {
                    stdout.push(b'\n'); // what wl-paste appends by default
                }
                Pasted {
                    success: true,
                    stdout,
                }
            }
            None => Pasted {
                success: false,
                stdout: Vec::new(),
            },
        })
    }

    fn copy(&self, args: &[&str], stdin: Option<&[u8]>) -> Result<bool, ClipboardError> {
        self.0.record("wl-copy", args);
        let mut offers = self.0.offers.borrow_mut();
        if args == ["--clear"] {
            offers.clear();
            return Ok(true);
        }
        assert_eq!(args.len(), 2, "unexpected wl-copy call {args:?}");
        assert_eq!(args[0], "--type");
        *offers = vec![(
            args[1].to_owned(),
            stdin.unwrap_or_default().to_vec(),
            false,
        )];
        Ok(true)
    }
}

fn entry_bytes(entry: &Value) -> Vec<u8> {
    if let Some(encoded) = entry["base64"].as_str() {
        return decode_base64(encoded);
    }
    entry["text"].as_str().unwrap().as_bytes().to_vec()
}

fn decode_base64(encoded: &str) -> Vec<u8> {
    const ALPHABET: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut bits = 0_u32;
    let mut count = 0;
    let mut out = Vec::new();
    for byte in encoded.bytes().filter(|byte| *byte != b'=') {
        let value = ALPHABET.iter().position(|a| *a == byte).unwrap() as u32;
        bits = (bits << 6) | value;
        count += 6;
        if count >= 8 {
            count -= 8;
            out.push((bits >> count) as u8);
        }
    }
    out
}

/// What Python's `InputBackend._type_clipboard` does, over the Rust adapter.
fn type_through(model: &Model, text: &str, restore: bool) -> Vec<(String, Vec<u8>)> {
    let clipboard = Clipboard::new(ModelPrograms(model));
    let saved = if restore {
        clipboard.save().unwrap()
    } else {
        None
    };
    clipboard.put(TEXT_MIME, text.as_bytes()).unwrap();
    model.calls.borrow_mut().push(json!("PASTE"));
    let pasted = model.state();
    if restore {
        match saved {
            Some(saved) => clipboard.put(&saved.mime, &saved.data).unwrap(),
            None => clipboard.clear().unwrap(),
        }
    }
    pasted
}

#[test]
fn every_fixture_case_runs_the_same_programs_as_python() {
    let fixture: Value = serde_json::from_str(FIXTURE).unwrap();
    assert_eq!(fixture["schema_version"], 1);
    assert_eq!(fixture["text_mime"], TEXT_MIME);
    let cases = fixture["cases"].as_array().unwrap();
    assert!(cases.len() >= 7);
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let model = Model::with(&case["initial"]);
        let text = case["text"].as_str().unwrap();
        let pasted = type_through(&model, text, case["restore"].as_bool().unwrap());

        assert_eq!(
            Value::Array(model.calls.borrow().clone()),
            case["calls"],
            "{name}: program calls"
        );
        assert_eq!(
            pasted,
            vec![(TEXT_MIME.to_owned(), text.as_bytes().to_vec())],
            "{name}: the paste must see exactly the typed text"
        );
        let expected: Vec<(String, Vec<u8>)> = case["final"]
            .as_array()
            .unwrap()
            .iter()
            .map(|entry| {
                (
                    entry["mime"].as_str().unwrap().to_owned(),
                    entry_bytes(entry),
                )
            })
            .collect();
        assert_eq!(model.state(), expected, "{name}: clipboard afterwards");
    }
}

// ------------------------------------------------------------ real processes

struct Scratch(PathBuf);

impl Scratch {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "pcbridge-clipboard-{}-{}",
            std::process::id(),
            NEXT_DIR.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir_all(&path).unwrap();
        Self(path)
    }

    fn script(&self, name: &str, body: &str) -> PathBuf {
        let path = self.0.join(name);
        fs::write(&path, format!("#!/bin/sh\n{body}")).unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
        path
    }

    fn read(&self, name: &str) -> Vec<u8> {
        fs::read(self.0.join(name)).unwrap_or_default()
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

fn quoted(path: &Path) -> String {
    format!("'{}'", path.display())
}

#[test]
fn wl_copy_gets_dev_null_and_returns_while_its_owner_lives_on() {
    let _turn = one_at_a_time();
    let scratch = Scratch::new();
    let dir = quoted(&scratch.0);
    // Like the real wl-copy: read stdin, leave a background owner behind that
    // inherits stdout and stderr, exit. The owner outlives the call by seconds.
    // The descriptors are read into variables first: `dash` applies a
    // redirection to the shell itself, so `readlink ... > file` saw the file.
    let copy = scratch.script(
        "wl-copy",
        &format!(
            "out=$(readlink /proc/$$/fd/1)\n\
             err=$(readlink /proc/$$/fd/2)\n\
             printf '%s\\n' \"$out\" > {dir}/stdout\n\
             printf '%s\\n' \"$err\" > {dir}/stderr\n\
             printf '%s\\n' \"$@\" > {dir}/args\n\
             cat > {dir}/stdin\n\
             ( sleep 3 ) &\n\
             exit 0\n"
        ),
    );
    let paste = scratch.script("wl-paste", "exit 1\n");
    let clipboard = Clipboard::new(SystemPrograms::with_programs(
        paste,
        copy,
        Duration::from_secs(10),
    ));
    let data = "pano: ğüşıöç İ\0ikili\n".as_bytes();

    let started = Instant::now();
    clipboard.put("text/plain;charset=utf-8", data).unwrap();
    let elapsed = started.elapsed();

    assert!(elapsed < Duration::from_millis(1500), "waited {elapsed:?}");
    assert_eq!(scratch.read("stdout"), b"/dev/null\n");
    assert_eq!(scratch.read("stderr"), b"/dev/null\n");
    assert_eq!(scratch.read("args"), b"--type\ntext/plain;charset=utf-8\n");
    assert_eq!(scratch.read("stdin"), data);
}

type Operation<'a> = dyn Fn() -> Result<(), ClipboardError> + 'a;

#[test]
fn a_program_that_hangs_is_killed_at_the_timeout() {
    let _turn = one_at_a_time();
    let scratch = Scratch::new();
    let paste = scratch.script("wl-paste", "exec sleep 30\n");
    let copy = scratch.script("wl-copy", "exec sleep 30\n");
    let clipboard = Clipboard::new(SystemPrograms::with_programs(
        paste,
        copy,
        Duration::from_millis(300),
    ));

    let operations: [(&str, &Operation); 3] = [
        ("save", &|| clipboard.save().map(|_| ())),
        ("put", &|| clipboard.put(TEXT_MIME, b"metin")),
        ("clear", &|| clipboard.clear()),
    ];
    for (operation, run) in operations {
        let started = Instant::now();
        let result = run();
        let elapsed = started.elapsed();
        assert!(
            matches!(result, Err(ClipboardError::Timeout { .. })),
            "{operation}: {result:?}"
        );
        assert!(
            elapsed >= Duration::from_millis(300) && elapsed < Duration::from_secs(2),
            "{operation} took {elapsed:?}"
        );
    }
}

#[test]
fn content_over_the_limit_is_refused_without_waiting_for_the_timeout() {
    let _turn = one_at_a_time();
    let scratch = Scratch::new();
    let paste = scratch.script(
        "wl-paste",
        "if [ \"$1\" = --list-types ]; then echo image/png; exit 0; fi\n\
         head -c 1000000 /dev/zero\n",
    );
    let copy = scratch.script("wl-copy", "exit 0\n");
    let clipboard = Clipboard::with_limit(
        SystemPrograms::with_programs(paste, copy, Duration::from_secs(10)),
        1000,
    );

    let started = Instant::now();
    let result = clipboard.save();
    assert!(
        matches!(result, Err(ClipboardError::TooLarge { limit: 1000 })),
        "{result:?}"
    );
    assert!(started.elapsed() < Duration::from_secs(2));
}

#[test]
fn missing_and_failing_programs_are_reported_not_ignored() {
    let _turn = one_at_a_time();
    let scratch = Scratch::new();
    let absent = Clipboard::new(SystemPrograms::with_programs(
        scratch.0.join("no-wl-paste"),
        scratch.0.join("no-wl-copy"),
        Duration::from_secs(1),
    ));
    assert!(matches!(
        absent.save(),
        Err(ClipboardError::Missing {
            program: "wl-paste"
        })
    ));
    assert!(matches!(
        absent.put(TEXT_MIME, b"x"),
        Err(ClipboardError::Missing { program: "wl-copy" })
    ));

    let failing = Clipboard::new(SystemPrograms::with_programs(
        scratch.script("wl-paste", "exit 1\n"),
        scratch.script("wl-copy", "cat > /dev/null\nexit 3\n"),
        Duration::from_secs(1),
    ));
    assert_eq!(
        failing.save().unwrap(),
        None,
        "an unreadable clipboard saves as empty"
    );
    assert!(matches!(
        failing.put(TEXT_MIME, b"x"),
        Err(ClipboardError::Failed { program: "wl-copy" })
    ));
    assert!(matches!(
        failing.clear(),
        Err(ClipboardError::Failed { program: "wl-copy" })
    ));
}

#[test]
fn an_invalid_mime_type_never_reaches_a_program() {
    let _turn = one_at_a_time();
    let scratch = Scratch::new();
    let copy = scratch.script("wl-copy", &format!("touch {}/ran\n", quoted(&scratch.0)));
    let clipboard = Clipboard::new(SystemPrograms::with_programs(
        scratch.script("wl-paste", "exit 1\n"),
        copy,
        Duration::from_secs(1),
    ));
    for mime in ["", "text/plain\n--clear", "a\u{7f}b"] {
        assert!(matches!(
            clipboard.put(mime, b"x"),
            Err(ClipboardError::InvalidMime)
        ));
    }
    assert!(!scratch.0.join("ran").exists());
}
