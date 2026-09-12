//! A real Mutter session feeding one real PipeWire frame into the PNG encoder.
//!
//! Skipped unless `PCBRIDGE_TEST_CAPTURE=1`: the test briefly opens a screen
//! share and reads pixels, but writes no image and sends no input.

use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::Instant;

use pcbridge_native::lifecycle::LifecycleFailure;
use pcbridge_native::platform::linux::capture::{CancelFlag, CaptureWorker};
use pcbridge_native::platform::linux::display::DisplayReader;
use pcbridge_native::platform::linux::pipewire_source::PipeWireFrameSource;
use pcbridge_native::platform::linux::session::{
    CaptureSession, CursorMode, MutterScreenCast, SessionGuard, StartRequest,
};

struct FlaggedRun(AtomicUsize);

impl SessionGuard for FlaggedRun {
    fn check(&self) -> Result<(), LifecycleFailure> {
        self.0.fetch_add(1, Ordering::AcqRel);
        Ok(())
    }
}

fn enabled() -> bool {
    std::env::var("PCBRIDGE_TEST_CAPTURE").as_deref() == Ok("1")
}

#[test]
fn real_pipewire_frames_are_nonblank_ordered_and_release_the_stream() {
    if !enabled() {
        eprintln!("skipped: set PCBRIDGE_TEST_CAPTURE=1 to read one real frame");
        return;
    }

    let display = DisplayReader::connect().expect("Mutter DisplayConfig");
    let snapshot = display.snapshot().expect("display snapshot");
    let monitor = snapshot.monitors.first().expect("at least one monitor");
    let request = StartRequest {
        monitors: vec![monitor.connector.clone()],
        cursor: CursorMode::Embedded,
        topology_id: snapshot.topology_id,
    };
    let guard = FlaggedRun(AtomicUsize::new(0));
    let mut session = CaptureSession::new(MutterScreenCast::connect().expect("Mutter ScreenCast"));
    session
        .open(&request, &guard)
        .expect("open capture session");
    let node = session
        .node_for(&monitor.connector)
        .expect("session should map the requested connector");

    let source = PipeWireFrameSource::new().expect("start PipeWire frame thread");
    let started = Instant::now();
    let captured = CaptureWorker::new(source.clone())
        .capture(node, &guard, &CancelFlag::new())
        .expect("capture one frame");
    let total_ms = started.elapsed().as_secs_f64() * 1000.0;
    let overhead_ms = total_ms
        - captured.waited.as_secs_f64() * 1000.0
        - captured.encoded_in.as_secs_f64() * 1000.0;

    assert_eq!(
        (captured.width, captured.height),
        (monitor.width, monitor.height)
    );
    assert!(captured.png.starts_with(b"\x89PNG\r\n\x1a\n"));
    let decoder = png::Decoder::new(std::io::Cursor::new(&captured.png));
    let mut reader = decoder.read_info().expect("PNG header");
    let mut pixels = vec![0; reader.output_buffer_size().expect("output size")];
    let info = reader.next_frame(&mut pixels).expect("PNG frame");
    pixels.truncate(info.buffer_size());
    assert!(
        pixels.chunks_exact(4).any(|pixel| pixel[..3] != [0, 0, 0]),
        "the real PNG decoded to an all-black placeholder"
    );
    assert!(pixels.chunks_exact(4).all(|pixel| pixel[3] == 0xFF));
    assert!(
        overhead_ms < 500.0,
        "stream teardown added {overhead_ms:.1} ms outside wait and encoding"
    );
    assert!(
        !source.is_attached(),
        "capture returned with a stream attached"
    );

    let second_started = Instant::now();
    let second = CaptureWorker::new(source.clone())
        .capture(node, &guard, &CancelFlag::new())
        .expect("capture a second frame from the same session and source");
    let second_total_ms = second_started.elapsed().as_secs_f64() * 1000.0;
    let second_overhead_ms = second_total_ms
        - second.waited.as_secs_f64() * 1000.0
        - second.encoded_in.as_secs_f64() * 1000.0;
    assert_eq!(
        (second.width, second.height),
        (monitor.width, monitor.height)
    );
    assert!(second.png.starts_with(b"\x89PNG\r\n\x1a\n"));
    assert!(second.id.sequence > captured.id.sequence);
    assert!(second.id.captured_at_ns > captured.id.captured_at_ns);
    assert_eq!(second.identity_source, captured.identity_source);
    assert!(second_overhead_ms < 500.0);
    assert!(
        !source.is_attached(),
        "second capture returned with a stream attached"
    );
    eprintln!(
        "connector={} node={} frame={}x{} first_png={} bytes first_sequence={} first_timestamp={} second_png={} bytes second_sequence={} second_timestamp={} source={} first_wait={:.1} ms first_encode={:.1} ms first_total={total_ms:.1} ms second_total={second_total_ms:.1} ms",
        monitor.connector,
        node,
        captured.width,
        captured.height,
        captured.png.len(),
        captured.id.sequence,
        captured.id.captured_at_ns,
        second.png.len(),
        second.id.sequence,
        second.id.captured_at_ns,
        captured.identity_source.as_str(),
        captured.waited.as_secs_f64() * 1000.0,
        captured.encoded_in.as_secs_f64() * 1000.0,
    );

    assert_eq!(session.stop(), Ok(true));
}
