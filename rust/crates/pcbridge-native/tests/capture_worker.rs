//! Capture worker against a scripted frame source.
//!
//! No PipeWire, no compositor, no display. The source is a trait so that the
//! sequences that decide whether a screenshot is trustworthy -- a frame that
//! predates the request, a stream that never produces one, a grant revoked
//! while we wait -- are tests rather than field reports.
//!
//! The fake records every call. Most assertions are about that log: what
//! matters on a failure is not only the error but that the stream went back.

use std::collections::VecDeque;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use pcbridge_core::frame::{FrameError, FrameId, FrameSpec, PixelFormat, RgbaFrame};
use pcbridge_native::lifecycle::LifecycleFailure;
use pcbridge_native::platform::linux::capture::{
    CancelFlag, CaptureError, CaptureWorker, FrameIdentitySource, FrameSource, SourceFrame,
    pixel_format_from_spa,
};
use pcbridge_native::platform::linux::pipewire_source::resolve_frame_identity;
use pcbridge_native::platform::linux::session::SessionGuard;
use pipewire::spa::param::video::VideoFormat;

// ---------------------------------------------------------------- fake source

/// What the source hands over next.
///
/// `Fresh` is stamped when it is delivered, the way a real source stamps a
/// frame as it arrives. `Stale` carries a stamp from before the request, which
/// is the only way to build the case the worker has to reject.
enum Scripted {
    Fresh(RgbaFrame),
    Stale(RgbaFrame),
    Failure(CaptureError),
}

#[derive(Default)]
struct Script {
    frames: VecDeque<Scripted>,
    attach_error: Option<CaptureError>,
}

#[derive(Clone)]
struct FakeSource {
    ops: Arc<Mutex<Vec<String>>>,
    script: Arc<Mutex<Script>>,
    detached_at: Arc<Mutex<Option<Instant>>>,
}

impl FakeSource {
    fn new() -> Self {
        Self {
            ops: Arc::new(Mutex::new(Vec::new())),
            script: Arc::new(Mutex::new(Script::default())),
            detached_at: Arc::new(Mutex::new(None)),
        }
    }

    fn ops(&self) -> Vec<String> {
        self.ops.lock().expect("ops lock").clone()
    }

    fn detached_at(&self) -> Option<Instant> {
        *self.detached_at.lock().expect("detach lock")
    }

    fn deliver(&self, frame: RgbaFrame) {
        self.push(Scripted::Fresh(frame));
    }

    fn deliver_stale(&self, frame: RgbaFrame) {
        self.push(Scripted::Stale(frame));
    }

    fn deliver_error(&self, error: CaptureError) {
        self.push(Scripted::Failure(error));
    }

    fn push(&self, scripted: Scripted) {
        self.script
            .lock()
            .expect("script lock")
            .frames
            .push_back(scripted);
    }

    fn fail_attach(&self, error: CaptureError) {
        self.script.lock().expect("script lock").attach_error = Some(error);
    }
}

impl FrameSource for FakeSource {
    fn attach(&self, node: u32) -> Result<(), CaptureError> {
        self.ops
            .lock()
            .expect("ops lock")
            .push(format!("attach:{node}"));
        match self
            .script
            .lock()
            .expect("script lock")
            .attach_error
            .clone()
        {
            Some(error) => Err(error),
            None => Ok(()),
        }
    }

    fn next_frame(&self, _deadline: Instant) -> Result<Option<SourceFrame>, CaptureError> {
        self.ops.lock().expect("ops lock").push("next".to_owned());
        // An empty script is the deadline: the fake never makes a test wait for
        // a real eight seconds.
        let scripted = self.script.lock().expect("script lock").frames.pop_front();
        match scripted {
            Some(Scripted::Fresh(frame)) => Ok(Some(SourceFrame {
                frame,
                received_at: Instant::now(),
                identity_source: FrameIdentitySource::SpaMetaHeader,
            })),
            Some(Scripted::Stale(frame)) => Ok(Some(SourceFrame {
                frame,
                received_at: Instant::now() - Duration::from_secs(30),
                identity_source: FrameIdentitySource::SpaMetaHeader,
            })),
            Some(Scripted::Failure(error)) => Err(error),
            None => Ok(None),
        }
    }

    fn detach(&self) {
        self.ops.lock().expect("ops lock").push("detach".to_owned());
        *self.detached_at.lock().expect("detach lock") = Some(Instant::now());
    }
}

// ----------------------------------------------------------------- fake guard

struct StepGuard {
    calls: AtomicUsize,
    fail_at: usize,
    failure: LifecycleFailure,
}

impl StepGuard {
    fn always_open() -> Self {
        Self {
            calls: AtomicUsize::new(0),
            fail_at: usize::MAX,
            failure: LifecycleFailure::Revoked,
        }
    }

    fn revoking_at(step: usize) -> Self {
        Self {
            calls: AtomicUsize::new(0),
            fail_at: step,
            failure: LifecycleFailure::Revoked,
        }
    }
}

impl SessionGuard for StepGuard {
    fn check(&self) -> Result<(), LifecycleFailure> {
        if self.calls.fetch_add(1, Ordering::AcqRel) >= self.fail_at {
            Err(self.failure)
        } else {
            Ok(())
        }
    }
}

// -------------------------------------------------------------------- helpers

/// A frame whose pixels are a gradient, so a blank or black result cannot pass.
fn frame(width: u32, height: u32, sequence: u64) -> RgbaFrame {
    let mut data = Vec::with_capacity((width * height * 4) as usize);
    for row in 0..height {
        for column in 0..width {
            data.extend_from_slice(&[
                (column % 251) as u8,
                (row % 241) as u8,
                ((row + column) % 239) as u8,
                0x00,
            ]);
        }
    }
    let spec = FrameSpec {
        format: PixelFormat::Bgrx,
        width,
        height,
        stride: width * 4,
        offset: 0,
        size: width * height * 4,
    };
    RgbaFrame::from_buffer(
        &spec,
        &data,
        FrameId {
            sequence,
            captured_at_ns: 4242,
        },
    )
    .expect("build the fixture frame")
}

fn decode(png: &[u8]) -> (u32, u32, Vec<u8>) {
    let decoder = png::Decoder::new(std::io::Cursor::new(png));
    let mut reader = decoder.read_info().expect("PNG header");
    let mut pixels = vec![0; reader.output_buffer_size().expect("output size")];
    let info = reader.next_frame(&mut pixels).expect("PNG frame");
    pixels.truncate((info.width * info.height * 4) as usize);
    (info.width, info.height, pixels)
}

fn worker(source: &FakeSource) -> CaptureWorker<FakeSource> {
    CaptureWorker::new(source.clone()).with_frame_timeout(Duration::from_millis(50))
}

// ---------------------------------------------------------------------- tests

#[test]
fn spa_formats_are_mapped_without_copying_header_constants() {
    assert_eq!(
        pixel_format_from_spa(VideoFormat::RGBA).expect("RGBA"),
        PixelFormat::Rgba
    );
    assert_eq!(
        pixel_format_from_spa(VideoFormat::RGBx).expect("RGBx"),
        PixelFormat::Rgbx
    );
    assert_eq!(
        pixel_format_from_spa(VideoFormat::BGRA).expect("BGRA"),
        PixelFormat::Bgra
    );
    assert_eq!(
        pixel_format_from_spa(VideoFormat::BGRx).expect("BGRx"),
        PixelFormat::Bgrx
    );

    let error = pixel_format_from_spa(VideoFormat::YUY2)
        .expect_err("an unimplemented SPA format must never be guessed");
    assert_eq!(
        error,
        CaptureError::UnsupportedFormat(VideoFormat::YUY2.as_raw())
    );
}

#[test]
fn producer_metadata_wins_when_pipewire_supplies_it() {
    let producer = FrameId {
        sequence: 91,
        captured_at_ns: 123_456,
    };

    let (id, source) = resolve_frame_identity(Some(producer), 7, 8);

    assert_eq!(id, producer);
    assert_eq!(source, FrameIdentitySource::SpaMetaHeader);
}

#[test]
fn source_monotonic_clock_is_an_explicit_fallback_when_metadata_is_absent() {
    let (id, source) = resolve_frame_identity(None, 7, 123_456);

    assert_eq!(
        id,
        FrameId {
            sequence: 7,
            captured_at_ns: 123_456,
        }
    );
    assert_eq!(source, FrameIdentitySource::SourceMonotonicClock);
}

#[test]
fn a_capture_returns_a_png_of_the_frame() {
    let source = FakeSource::new();
    source.deliver(frame(4, 3, 11));

    let captured = worker(&source)
        .capture(79, &StepGuard::always_open(), &CancelFlag::new())
        .expect("capture");

    assert_eq!(captured.width, 4);
    assert_eq!(captured.height, 3);
    assert_eq!(captured.id.sequence, 11);
    assert_eq!(captured.id.captured_at_ns, 4242);
    assert_eq!(captured.identity_source, FrameIdentitySource::SpaMetaHeader);
    assert_eq!(captured.stale_frames, 0);
    assert_eq!(source.ops(), vec!["attach:79", "next", "detach"]);

    let (width, height, pixels) = decode(&captured.png);
    assert_eq!((width, height), (4, 3));
    // Blue and red were exchanged and the padding byte became opaque alpha.
    assert_eq!(&pixels[..4], &[0, 0, 0, 0xFF]);
    assert_eq!(&pixels[4..8], &[1, 0, 1, 0xFF]);
    assert!(
        pixels.chunks_exact(4).any(|pixel| pixel[..3] != [0, 0, 0]),
        "the PNG decoded to an all-black image"
    );
}

#[test]
fn the_stream_is_released_before_the_png_is_encoded() {
    // Big enough that encoding takes real time: if the encode ran before the
    // detach, the detach stamp would fall inside the encoding window instead of
    // before it.
    let source = FakeSource::new();
    source.deliver(frame(400, 400, 1));

    let captured = worker(&source)
        .capture(79, &StepGuard::always_open(), &CancelFlag::new())
        .expect("capture");
    let returned_at = Instant::now();

    assert!(
        captured.encoded_in > Duration::ZERO,
        "encoding was not measured"
    );
    let detached_at = source.detached_at().expect("detach was recorded");
    assert!(
        detached_at <= returned_at - captured.encoded_in,
        "the stream was still attached while the PNG was encoded"
    );
    assert_eq!(source.ops().last().map(String::as_str), Some("detach"));
}

#[test]
fn a_frame_older_than_the_request_is_dropped_and_counted() {
    let source = FakeSource::new();
    source.deliver_stale(frame(2, 2, 1));
    source.deliver_stale(frame(2, 2, 2));
    source.deliver(frame(2, 2, 3));

    let captured = worker(&source)
        .capture(79, &StepGuard::always_open(), &CancelFlag::new())
        .expect("capture");

    assert_eq!(
        captured.id.sequence, 3,
        "an older frame answered the request"
    );
    assert_eq!(captured.stale_frames, 2);
}

#[test]
fn a_stream_that_never_produces_a_frame_times_out_and_detaches() {
    let source = FakeSource::new();

    let error = worker(&source)
        .capture(79, &StepGuard::always_open(), &CancelFlag::new())
        .expect_err("a silent stream must not hang");

    assert_eq!(error, CaptureError::Timeout(Duration::from_millis(50)));
    assert_eq!(source.ops(), vec!["attach:79", "next", "detach"]);
}

#[test]
fn a_stream_failure_detaches_and_says_why() {
    let source = FakeSource::new();
    source.deliver_error(CaptureError::Stream("node disappeared".to_owned()));

    let error = worker(&source)
        .capture(79, &StepGuard::always_open(), &CancelFlag::new())
        .expect_err("a broken stream must fail the capture");

    assert_eq!(error, CaptureError::Stream("node disappeared".to_owned()));
    assert_eq!(source.ops().last().map(String::as_str), Some("detach"));
}

#[test]
fn an_unreadable_frame_is_refused_rather_than_guessed() {
    let source = FakeSource::new();
    source.deliver_error(CaptureError::Frame(FrameError::StrideTooSmall {
        stride: 8,
        needed: 16,
    }));

    let error = worker(&source)
        .capture(79, &StepGuard::always_open(), &CancelFlag::new())
        .expect_err("a malformed buffer must not become an image");

    assert!(matches!(error, CaptureError::Frame(_)));
    assert_eq!(source.ops().last().map(String::as_str), Some("detach"));
}

#[test]
fn a_failed_attach_still_releases_the_stream() {
    let source = FakeSource::new();
    source.fail_attach(CaptureError::Unavailable("no such node".to_owned()));

    let error = worker(&source)
        .capture(79, &StepGuard::always_open(), &CancelFlag::new())
        .expect_err("attach failed");

    assert_eq!(error, CaptureError::Unavailable("no such node".to_owned()));
    assert_eq!(source.ops(), vec!["attach:79", "detach"]);
}

#[test]
fn a_cancel_before_the_capture_never_touches_the_stream() {
    let source = FakeSource::new();
    let cancel = CancelFlag::new();
    cancel.cancel();

    let error = worker(&source)
        .capture(79, &StepGuard::always_open(), &cancel)
        .expect_err("a canceled capture must not start");

    assert_eq!(error, CaptureError::Canceled);
    assert!(source.ops().is_empty());
}

#[test]
fn a_cancel_while_waiting_stops_the_capture_and_detaches() {
    let source = FakeSource::new();
    let cancel = CancelFlag::new();
    // The guard is the only hook that runs inside the wait loop, so cancel from
    // there: it is the same moment a caller's cancel would land.
    struct CancelingGuard(CancelFlag);
    impl SessionGuard for CancelingGuard {
        fn check(&self) -> Result<(), LifecycleFailure> {
            self.0.cancel();
            Ok(())
        }
    }

    let error = worker(&source)
        .capture(79, &CancelingGuard(cancel.clone()), &cancel)
        .expect_err("a canceled capture must stop");

    assert_eq!(error, CaptureError::Canceled);
    assert_eq!(source.ops().last().map(String::as_str), Some("detach"));
}

#[test]
fn a_revoked_grant_never_attaches() {
    let source = FakeSource::new();

    let error = worker(&source)
        .capture(79, &StepGuard::revoking_at(0), &CancelFlag::new())
        .expect_err("a revoked grant must not capture");

    assert_eq!(error, CaptureError::Guard(LifecycleFailure::Revoked));
    assert!(source.ops().is_empty());
}

#[test]
fn a_revoke_after_the_frame_arrives_produces_no_png() {
    let source = FakeSource::new();
    source.deliver(frame(4, 4, 1));

    // Checkpoints: 0 before attach, 1 inside the wait loop, 2 after detach.
    let error = worker(&source)
        .capture(79, &StepGuard::revoking_at(2), &CancelFlag::new())
        .expect_err("a revoke during the wait must not yield an image");

    assert_eq!(error, CaptureError::Guard(LifecycleFailure::Revoked));
    assert_eq!(source.ops(), vec!["attach:79", "next", "detach"]);
}

#[test]
fn every_failure_path_detaches_exactly_once() {
    #[derive(Debug)]
    enum Case {
        Timeout,
        StreamError,
        AttachError,
    }

    for case in [Case::Timeout, Case::StreamError, Case::AttachError] {
        let source = FakeSource::new();
        match case {
            Case::Timeout => {}
            Case::StreamError => source.deliver_error(CaptureError::Stream("gone".to_owned())),
            Case::AttachError => {
                source.fail_attach(CaptureError::Unavailable("gone".to_owned()));
            }
        }

        let _ = worker(&source).capture(79, &StepGuard::always_open(), &CancelFlag::new());

        assert_eq!(
            source.ops().iter().filter(|op| *op == "detach").count(),
            1,
            "{case:?} did not detach exactly once"
        );
    }
}
