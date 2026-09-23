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
//! A frame carries an explicit identity and the source of that identity. Mutter
//! supplies no `SPA_META_Header` on the measured machine, so that path uses a
//! source-local sequence and monotonic clock; producers that do supply the
//! header retain its sequence and PTS unchanged. Neither clock decides whether
//! the frame answers *this* request. Instead the source stamps each frame with
//! a separate monotonic `Instant` as it arrives, and anything older than the
//! request is dropped and counted.

use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use pcbridge_core::frame::{FrameError, FrameId, RgbaFrame};
use pipewire::spa::param::video::VideoFormat;

use crate::lifecycle::Lifecycle;
use crate::lifecycle::LifecycleFailure;
use crate::platform::linux::desktop::DesktopKind;
use crate::platform::linux::display::DisplaySnapshot;
use crate::platform::linux::kwin_screenshot::KWinScreenShot;
use crate::platform::linux::pipewire_source::PipeWireFrameSource;
use crate::platform::linux::session::{
    CaptureSession, CursorMode, MutterScreenCast, OpenOutcome, SessionFailure, SessionGuard,
    SessionHandle, StartRequest,
};

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
    /// States whether `frame.id` came from producer metadata or the explicit
    /// source-monotonic fallback.
    pub identity_source: FrameIdentitySource,
}

/// Provenance of the sequence and timestamp carried in `FrameId`.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum FrameIdentitySource {
    /// Sequence and PTS copied from `SPA_META_Header` without conversion.
    SpaMetaHeader,
    /// Source-local sequence plus nanoseconds since this source started.
    SourceMonotonicClock,
}

impl FrameIdentitySource {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::SpaMetaHeader => "spa_meta_header",
            Self::SourceMonotonicClock => "source_monotonic_clock",
        }
    }
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
    #[error("SPA video format {0} is unsupported")]
    UnsupportedFormat(u32),
}

/// Convert the SPA format type obtained from the installed headers into the
/// platform-independent packed layout understood by `pcbridge-core`.
pub fn pixel_format_from_spa(
    format: VideoFormat,
) -> Result<pcbridge_core::frame::PixelFormat, CaptureError> {
    use pcbridge_core::frame::PixelFormat;

    match format {
        VideoFormat::RGBA => Ok(PixelFormat::Rgba),
        VideoFormat::RGBx => Ok(PixelFormat::Rgbx),
        VideoFormat::BGRA => Ok(PixelFormat::Bgra),
        VideoFormat::BGRx => Ok(PixelFormat::Bgrx),
        other => Err(CaptureError::UnsupportedFormat(other.as_raw())),
    }
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
    pub identity_source: FrameIdentitySource,
    /// Frames that arrived but predated the request. Non-zero means the stream
    /// was already running; it is reported rather than hidden.
    pub stale_frames: u32,
    pub waited: Duration,
    pub encoded_in: Duration,
}

#[derive(Debug, thiserror::Error)]
pub enum NativeCaptureError {
    #[error("the display layout changed before capture")]
    DisplayChanged,
    #[error("display connector '{0}' is not in the current layout")]
    DisplayUnknown(String),
    #[error(transparent)]
    Session(#[from] SessionFailure),
    #[error(transparent)]
    Capture(#[from] CaptureError),
}

/// A captured monitor plus the snapshot geometry that selected it.
#[derive(Debug)]
pub struct CapturedMonitor {
    pub image: CapturedPng,
    pub x: i32,
    pub y: i32,
    pub expected_width: u32,
    pub expected_height: u32,
}

/// The reusable production capture resources.
///
/// On GNOME the Mutter session stays open under the grant, while the PipeWire
/// consumer attaches only for the duration of one frame. `Lifecycle` owns a
/// second reference to the session handle so its watchdog can close the
/// visible share immediately on revoke or lock. On KDE Plasma every frame is
/// one `ScreenShot2` call and nothing stays open.
#[derive(Debug)]
pub struct NativeCapture {
    backend: CaptureBackend,
}

#[derive(Debug)]
enum CaptureBackend {
    Mutter {
        session: Arc<SessionHandle<MutterScreenCast>>,
        source: PipeWireFrameSource,
    },
    KWin(KWinScreenShot),
}

impl NativeCapture {
    pub fn connect(lifecycle: &Lifecycle) -> Result<Self, NativeCaptureError> {
        if DesktopKind::detect() == DesktopKind::Kde {
            return Ok(Self {
                backend: CaptureBackend::KWin(KWinScreenShot::connect()?),
            });
        }
        let session = Arc::new(SessionHandle::new(CaptureSession::new(
            MutterScreenCast::connect().map_err(|error| {
                CaptureError::Unavailable(format!("Mutter ScreenCast unavailable: {error}"))
            })?,
        )));
        let source = PipeWireFrameSource::new()?;
        lifecycle.register_fail_closed(session.clone());
        Ok(Self {
            backend: CaptureBackend::Mutter { session, source },
        })
    }

    /// The backend name reported with every frame and session.
    #[must_use]
    pub const fn backend_name(&self) -> &'static str {
        match self.backend {
            CaptureBackend::Mutter { .. } => MUTTER_BACKEND,
            CaptureBackend::KWin(_) => KWIN_BACKEND,
        }
    }

    /// The `display_id` prefix this backend accepts (`mutter:` / `kwin:`).
    #[must_use]
    pub const fn display_scheme(&self) -> &'static str {
        match self.backend {
            CaptureBackend::Mutter { .. } => "mutter",
            CaptureBackend::KWin(_) => "kwin",
        }
    }

    /// Open, reuse or recreate the Mutter session for every monitor in
    /// `snapshot`, without reading a frame.
    ///
    /// `desktop_unlock` reaches this (Task 4.3), so the sharing indicator
    /// appears with the grant, exactly when the Python path shows it.
    /// `capture` goes through the same call, so the two cannot build
    /// different sessions. KWin has no session: only the layout and the
    /// grant are checked.
    pub fn open_session(
        &self,
        snapshot: &DisplaySnapshot,
        topology_id: &str,
        include_pointer: bool,
        lifecycle: &Lifecycle,
    ) -> Result<OpenOutcome, NativeCaptureError> {
        if snapshot.topology_id != topology_id {
            return Err(NativeCaptureError::DisplayChanged);
        }
        let CaptureBackend::Mutter { session, .. } = &self.backend else {
            lifecycle
                .check()
                .map_err(|failure| NativeCaptureError::Capture(CaptureError::Guard(failure)))?;
            return Ok(OpenOutcome::NotNeeded);
        };
        let request = StartRequest {
            monitors: snapshot
                .monitors
                .iter()
                .map(|monitor| monitor.connector.clone())
                .collect(),
            cursor: CursorMode::embedded(include_pointer),
            topology_id: snapshot.topology_id.clone(),
        };
        session
            .open(&request, lifecycle)
            .map_err(NativeCaptureError::from)
    }

    pub fn capture(
        &self,
        snapshot: &DisplaySnapshot,
        topology_id: &str,
        connector: &str,
        include_pointer: bool,
        timeout: Duration,
        lifecycle: &Lifecycle,
    ) -> Result<CapturedMonitor, NativeCaptureError> {
        if snapshot.topology_id != topology_id {
            return Err(NativeCaptureError::DisplayChanged);
        }
        let monitor = snapshot
            .monitors
            .iter()
            .find(|monitor| monitor.connector == connector)
            .ok_or_else(|| NativeCaptureError::DisplayUnknown(connector.to_owned()))?;
        let image = match &self.backend {
            CaptureBackend::Mutter { session, source } => {
                self.open_session(snapshot, topology_id, include_pointer, lifecycle)?;
                let node = session
                    .node_for(connector)
                    .ok_or_else(|| NativeCaptureError::DisplayUnknown(connector.to_owned()))?;
                match CaptureWorker::new(source.clone())
                    .with_frame_timeout(timeout)
                    .capture(node, lifecycle, &CancelFlag::new())
                {
                    Ok(image) => image,
                    Err(error) => {
                        // A frame timeout or broken node invalidates the
                        // session/node map. A retry must negotiate a new one,
                        // not reuse stale ids.
                        let _ = session.stop();
                        return Err(error.into());
                    }
                }
            }
            CaptureBackend::KWin(kwin) => {
                capture_kwin(kwin, connector, include_pointer, lifecycle)?
            }
        };
        Ok(CapturedMonitor {
            image,
            x: monitor.x,
            y: monitor.y,
            expected_width: monitor.width,
            expected_height: monitor.height,
        })
    }
}

pub const MUTTER_BACKEND: &str = "linux.mutter.pipewire";
pub const KWIN_BACKEND: &str = "linux.kwin.screenshot2";

/// One `ScreenShot2` frame under the grant: checked before the call and
/// again before it becomes a PNG, like the PipeWire worker.
fn capture_kwin(
    kwin: &KWinScreenShot,
    connector: &str,
    include_pointer: bool,
    guard: &dyn SessionGuard,
) -> Result<CapturedPng, CaptureError> {
    let requested_at = Instant::now();
    guard.check().map_err(CaptureError::Guard)?;
    let SourceFrame {
        frame,
        received_at,
        identity_source,
    } = kwin.capture_screen(connector, include_pointer)?;
    guard.check().map_err(CaptureError::Guard)?;
    let started = Instant::now();
    let png = frame.to_png()?;
    Ok(CapturedPng {
        png,
        width: frame.width,
        height: frame.height,
        id: frame.id,
        identity_source,
        stale_frames: 0,
        waited: received_at.saturating_duration_since(requested_at),
        encoded_in: started.elapsed(),
    })
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
        let (source_frame, stale_frames, waited) = collected?;

        // A revoke that landed while we were waiting must not turn into a PNG.
        self.checkpoint(guard, cancel)?;

        let SourceFrame {
            frame,
            identity_source,
            ..
        } = source_frame;
        let started = Instant::now();
        let png = frame.to_png()?;
        Ok(CapturedPng {
            png,
            width: frame.width,
            height: frame.height,
            id: frame.id,
            identity_source,
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
    ) -> Result<(SourceFrame, u32, Duration), CaptureError> {
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
            return Ok((source_frame, stale_frames, requested_at.elapsed()));
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
