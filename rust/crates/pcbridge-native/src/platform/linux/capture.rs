//! Taking one frame from a screencast session's PipeWire stream.
//!
//! The session from `session.rs` produces a node id per connector. This module
//! turns a node id into a PNG, on demand: attach to the stream, take the first
//! frame that belongs to *this* request, release everything, and only then
//! encode.
//!
//! ## Why the order matters
//!
//! A PipeWire frame arrives as memory the producer still owns. Whatever we do
//! before handing that buffer back, the compositor waits for -- and PNG
//! encoding a 1920x1080 frame is tens of milliseconds of pure CPU. Encoding
//! inside that window would make a screenshot cost the whole desktop a stall.
//!
//! So the split is structural rather than a rule to remember: `FrameSource`
//! returns an **owned** `RgbaFrame`, which means the conversion already happened
//! and the producer's buffer is already back. By the time `to_png` runs there is
//! nothing left to block. The worker also calls `detach` before encoding, so the
//! stream is released first as well.
//!
//! ## Why freshness is measured on our clock
//!
//! A frame carries the producer's sequence number and presentation timestamp,
//! and both are reported to the caller. Neither decides whether the frame
//! answers *this* request: that timestamp comes from the driver's clock, and
//! assuming it is the same clock as ours is exactly the kind of unmeasured
//! assumption that produces a screenshot of the previous screen. Instead the
//! source stamps each frame with a monotonic `Instant` as it arrives, and
//! anything older than the request is dropped and counted.
//!
//! ## What is not here yet
//!
//! The real `FrameSource` -- the one that owns a PipeWire loop on a dedicated
//! thread -- needs `libpipewire-0.3-dev` to build, which is not installed on
//! this machine. Everything above that line is written and tested against a
//! fake in `tests/capture_worker.rs`; the PipeWire implementation lands behind
//! this same trait.

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use pcbridge_core::frame::{FrameError, FrameId, RgbaFrame};

use crate::lifecycle::LifecycleFailure;
use crate::platform::linux::session::SessionGuard;

/// How long a capture waits for a frame.
///
/// Same eight seconds the Python helper allows, for the same reason: no MCP
/// call may block longer than 110 seconds and this path sits inside one. A
/// monitor that went to sleep stops answering without ever failing, so the wait
/// has to end by itself.
pub const FRAME_TIMEOUT: Duration = Duration::from_secs(8);

/// A frame that has already left the producer's memory.
///
/// Owning the pixels is the point: nothing downstream can accidentally hold a
/// compositor buffer open.
#[derive(Clone, Debug)]
pub struct SourceFrame {
    pub frame: RgbaFrame,
    /// Stamped by the source from our own monotonic clock as the frame arrived.
    pub received_at: Instant,
}

#[derive(Clone, Debug, Eq, PartialEq, thiserror::Error)]
pub enum CaptureError {
    #[error("desktop grant is not usable: {}", .0.code())]
    Guard(LifecycleFailure),
    #[error("no frame arrived within {0:?}")]
    Timeout(Duration),
    #[error("the capture was canceled")]
    Canceled,
    #[error(transparent)]
    Frame(#[from] FrameError),
    #[error("capture backend unavailable: {0}")]
    Unavailable(String),
    #[error("the stream failed: {0}")]
    Stream(String),
}

/// The PipeWire side of a capture.
///
/// Implementations own a single dedicated thread for the PipeWire loop: that
/// loop is not shareable, and running it per request would pay the stream
/// setup cost on every screenshot.
pub trait FrameSource {
    /// Start consuming `node`. Called once per capture.
    fn attach(&self, node: u32) -> Result<(), CaptureError>;

    /// The next frame, or `None` once `deadline` has passed.
    ///
    /// The returned pixels are owned and the producer's buffer is already
    /// released -- converting and copying happens here, while the memory is
    /// still mapped, and nothing else may.
    fn next_frame(&self, deadline: Instant) -> Result<Option<SourceFrame>, CaptureError>;

    /// Release the stream and every buffer taken from it. Must be safe to call
    /// when nothing is attached, and must not fail: it runs on the error paths.
    fn detach(&self);
}

/// Cooperative cancellation, shared with whoever wants to stop a capture.
#[derive(Clone, Debug, Default)]
pub struct CancelFlag(Arc<AtomicBool>);

impl CancelFlag {
    #[must_use]
    pub fn new() -> Self {
        Self(Arc::new(AtomicBool::new(false)))
    }

    pub fn cancel(&self) {
        self.0.store(true, Ordering::Release);
    }

    #[must_use]
    pub fn is_canceled(&self) -> bool {
        self.0.load(Ordering::Acquire)
    }
}

/// A PNG plus what it took to get it.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CapturedPng {
    pub png: Vec<u8>,
    pub width: u32,
    pub height: u32,
    pub id: FrameId,
    /// Frames that arrived but predated the request. Non-zero means the stream
    /// was already running; it is reported rather than hidden.
    pub stale_frames: u32,
    pub waited: Duration,
    pub encoded_in: Duration,
}

/// Turns a node id into a PNG, under the desktop grant.
#[derive(Debug)]
pub struct CaptureWorker<S: FrameSource> {
    source: S,
    frame_timeout: Duration,
}

impl<S: FrameSource> CaptureWorker<S> {
    #[must_use]
    pub const fn new(source: S) -> Self {
        Self {
            source,
            frame_timeout: FRAME_TIMEOUT,
        }
    }

    #[must_use]
    pub const fn with_frame_timeout(mut self, timeout: Duration) -> Self {
        self.frame_timeout = timeout;
        self
    }

    /// Capture one frame from `node`.
    ///
    /// `node` must come from a `Ready` session's connector map; this function
    /// does not guess one. The stream is detached on every path out, including
    /// the failures.
    pub fn capture(
        &self,
        node: u32,
        guard: &dyn SessionGuard,
        cancel: &CancelFlag,
    ) -> Result<CapturedPng, CaptureError> {
        let requested_at = Instant::now();
        self.checkpoint(guard, cancel)?;
        if let Err(error) = self.source.attach(node) {
            // A half-built stream is still a stream. Same lesson as the session
            // start: release whatever the failed attempt created.
            self.source.detach();
            return Err(error);
        }

        let collected = self.collect(requested_at, guard, cancel);
        // Before the error is even looked at: the stream goes back first, and
        // it goes back before anything expensive runs.
        self.source.detach();
        let (frame, stale_frames, waited) = collected?;

        // A revoke that landed while we were waiting must not turn into a PNG.
        self.checkpoint(guard, cancel)?;

        let started = Instant::now();
        let png = frame.to_png()?;
        Ok(CapturedPng {
            png,
            width: frame.width,
            height: frame.height,
            id: frame.id,
            stale_frames,
            waited,
            encoded_in: started.elapsed(),
        })
    }

    fn collect(
        &self,
        requested_at: Instant,
        guard: &dyn SessionGuard,
        cancel: &CancelFlag,
    ) -> Result<(RgbaFrame, u32, Duration), CaptureError> {
        let deadline = requested_at + self.frame_timeout;
        let mut stale_frames = 0;
        loop {
            self.checkpoint(guard, cancel)?;
            let Some(source_frame) = self.source.next_frame(deadline)? else {
                return Err(CaptureError::Timeout(self.frame_timeout));
            };
            if source_frame.received_at < requested_at {
                // Composited before we asked. Answering with it would show the
                // screen as it was, which is worse than waiting.
                stale_frames += 1;
                continue;
            }
            return Ok((source_frame.frame, stale_frames, requested_at.elapsed()));
        }
    }

    fn checkpoint(
        &self,
        guard: &dyn SessionGuard,
        cancel: &CancelFlag,
    ) -> Result<(), CaptureError> {
        if cancel.is_canceled() {
            return Err(CaptureError::Canceled);
        }
        guard.check().map_err(CaptureError::Guard)
    }
}
