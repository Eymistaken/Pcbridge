//! Direct PipeWire frame transport for an on-demand Mutter capture.
//!
//! PipeWire objects are deliberately confined to one long-lived thread. The
//! public source sends attach/detach commands to that loop and receives owned
//! frames over a bounded channel; no PipeWire object crosses a thread boundary.

use std::cell::RefCell;
use std::fmt;
use std::io::Cursor;
use std::rc::Rc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, Receiver, SyncSender, TrySendError};
use std::sync::{Arc, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use pcbridge_core::frame::{FrameId, FrameSpec, RgbaFrame};
use pipewire as pw;
use pw::properties::properties;
use pw::spa;
use spa::buffer::ChunkFlags;
use spa::buffer::meta::{MetaHeader, MetaHeaderFlags};
use spa::param::video::{VideoFormat, VideoInfoRaw};
use spa::pod::Pod;

use crate::platform::linux::capture::{
    CaptureError, FrameIdentitySource, FrameSource, SourceFrame, pixel_format_from_spa,
};

const CONTROL_TIMEOUT: Duration = Duration::from_secs(2);
const STARTUP_TIMEOUT: Duration = Duration::from_secs(8);
const FRAME_QUEUE_DEPTH: usize = 2;

#[derive(Clone, Copy, Debug)]
struct NegotiatedFormat {
    format: pcbridge_core::frame::PixelFormat,
    width: u32,
    height: u32,
}

#[derive(Debug)]
enum WorkerEvent {
    Frame(SourceFrame),
    Error(CaptureError),
}

enum WorkerCommand {
    Attach {
        node: u32,
        reply: mpsc::Sender<Result<(), CaptureError>>,
    },
    Detach {
        reply: mpsc::Sender<()>,
    },
    Shutdown {
        reply: mpsc::Sender<()>,
    },
}

struct LoopState {
    format: Option<NegotiatedFormat>,
    local_sequence: u64,
    clock_origin: Instant,
    events: SyncSender<WorkerEvent>,
    attached: Arc<AtomicBool>,
}

struct SourceInner {
    commands: pw::channel::Sender<WorkerCommand>,
    events: Mutex<Receiver<WorkerEvent>>,
    attached: Arc<AtomicBool>,
    thread: Mutex<Option<JoinHandle<()>>>,
}

impl Drop for SourceInner {
    fn drop(&mut self) {
        let (reply, received) = mpsc::channel();
        let _ = self.commands.send(WorkerCommand::Shutdown { reply });
        let _ = received.recv_timeout(CONTROL_TIMEOUT);
        self.attached.store(false, Ordering::Release);
        if let Some(thread) = self
            .thread
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .take()
        {
            let _ = thread.join();
        }
    }
}

/// A reusable source whose single PipeWire loop lives on a dedicated thread.
///
/// Clones share that thread. A capture attaches one node, copies one frame out
/// of producer-owned memory, then detaches before PNG encoding begins.
#[derive(Clone)]
pub struct PipeWireFrameSource {
    inner: Arc<SourceInner>,
}

impl fmt::Debug for PipeWireFrameSource {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("PipeWireFrameSource")
            .field("attached", &self.is_attached())
            .finish_non_exhaustive()
    }
}

impl PipeWireFrameSource {
    pub fn new() -> Result<Self, CaptureError> {
        let (commands, command_receiver) = pw::channel::channel();
        let (event_sender, events) = mpsc::sync_channel(FRAME_QUEUE_DEPTH);
        let (startup_sender, startup) = mpsc::channel();
        let attached = Arc::new(AtomicBool::new(false));
        let thread_attached = Arc::clone(&attached);

        let thread = thread::Builder::new()
            .name(format!("pcbridge-pipewire-{}", std::process::id()))
            .spawn(move || {
                run_pipewire_loop(
                    command_receiver,
                    event_sender,
                    thread_attached,
                    startup_sender,
                );
            })
            .map_err(|error| CaptureError::Unavailable(error.to_string()))?;

        match startup.recv_timeout(STARTUP_TIMEOUT) {
            Ok(Ok(())) => Ok(Self {
                inner: Arc::new(SourceInner {
                    commands,
                    events: Mutex::new(events),
                    attached,
                    thread: Mutex::new(Some(thread)),
                }),
            }),
            Ok(Err(error)) => {
                let _ = thread.join();
                Err(error)
            }
            Err(_) => {
                let (reply, received) = mpsc::channel();
                let _ = commands.send(WorkerCommand::Shutdown { reply });
                let _ = received.recv_timeout(CONTROL_TIMEOUT);
                let _ = thread.join();
                Err(CaptureError::Unavailable(
                    "PipeWire loop did not start before its deadline".to_owned(),
                ))
            }
        }
    }

    #[must_use]
    pub fn is_attached(&self) -> bool {
        self.inner.attached.load(Ordering::Acquire)
    }

    fn clear_events(&self) {
        let events = self
            .inner
            .events
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        while events.try_recv().is_ok() {}
    }
}

impl FrameSource for PipeWireFrameSource {
    fn attach(&self, node: u32) -> Result<(), CaptureError> {
        self.clear_events();
        let (reply, received) = mpsc::channel();
        self.inner
            .commands
            .send(WorkerCommand::Attach { node, reply })
            .map_err(|_| CaptureError::Unavailable("PipeWire loop stopped".to_owned()))?;
        received
            .recv_timeout(CONTROL_TIMEOUT)
            .map_err(|_| CaptureError::Unavailable("PipeWire attach timed out".to_owned()))?
    }

    fn next_frame(&self, deadline: Instant) -> Result<Option<SourceFrame>, CaptureError> {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Ok(None);
        }
        let events = self
            .inner
            .events
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        match events.recv_timeout(remaining) {
            Ok(WorkerEvent::Frame(frame)) => Ok(Some(frame)),
            Ok(WorkerEvent::Error(error)) => Err(error),
            Err(mpsc::RecvTimeoutError::Timeout) => Ok(None),
            Err(mpsc::RecvTimeoutError::Disconnected) => Err(CaptureError::Unavailable(
                "PipeWire frame thread stopped".to_owned(),
            )),
        }
    }

    fn detach(&self) {
        if !self.is_attached() {
            self.clear_events();
            return;
        }
        let (reply, received) = mpsc::channel();
        if self
            .inner
            .commands
            .send(WorkerCommand::Detach { reply })
            .is_ok()
        {
            let _ = received.recv_timeout(CONTROL_TIMEOUT);
        }
        self.inner.attached.store(false, Ordering::Release);
        self.clear_events();
    }
}

fn run_pipewire_loop(
    commands: pw::channel::Receiver<WorkerCommand>,
    events: SyncSender<WorkerEvent>,
    attached: Arc<AtomicBool>,
    startup: mpsc::Sender<Result<(), CaptureError>>,
) {
    let result = (|| -> Result<(), CaptureError> {
        pw::init();
        let mainloop = pw::main_loop::MainLoopRc::new(None)
            .map_err(|error| CaptureError::Unavailable(error.to_string()))?;
        let context = pw::context::ContextRc::new(&mainloop, None)
            .map_err(|error| CaptureError::Unavailable(error.to_string()))?;
        let core = context
            .connect_rc(None)
            .map_err(|error| CaptureError::Unavailable(error.to_string()))?;

        let state = Rc::new(RefCell::new(LoopState {
            format: None,
            local_sequence: 0,
            clock_origin: Instant::now(),
            events,
            attached: Arc::clone(&attached),
        }));
        // Declared after `core`, so it is dropped first: a stream never
        // outlives the connection it was created on.
        let active: Rc<RefCell<Option<ActiveStream>>> = Rc::new(RefCell::new(None));

        let command_core = core.clone();
        let command_state = Rc::clone(&state);
        let command_active = Rc::clone(&active);
        let command_loop = mainloop.clone();
        let _commands = commands.attach(mainloop.loop_(), move |command| match command {
            WorkerCommand::Attach { node, reply } => {
                let result = attach_stream(&command_core, &command_state, &command_active, node);
                let _ = reply.send(result);
            }
            WorkerCommand::Detach { reply } => {
                detach_stream(&command_state, &command_active);
                let _ = reply.send(());
            }
            WorkerCommand::Shutdown { reply } => {
                detach_stream(&command_state, &command_active);
                let _ = reply.send(());
                command_loop.quit();
            }
        });

        startup
            .send(Ok(()))
            .map_err(|_| CaptureError::Unavailable("PipeWire owner disappeared".to_owned()))?;
        mainloop.run();
        detach_stream(&state, &active);
        Ok(())
    })();

    attached.store(false, Ordering::Release);
    if let Err(error) = result {
        let _ = startup.send(Err(error));
    }
}

/// The stream of one capture, created for exactly one node.
///
/// Never reused for another node. Measured 2026-09-13 (PipeWire 1.0.5,
/// WirePlumber 0.4.17, two monitors): one long-lived `pw_stream` reconnected
/// to a different node kept delivering the node it was FIRST connected to.
/// With DP-3 requested first, every later DP-4 request returned DP-3's
/// picture, and the other way round when DP-4 came first -- a frame from the
/// wrong screen with every size check passing. The legacy GStreamer helper
/// never hit this because it builds a new `pipewiresrc` for every capture.
struct ActiveStream {
    // Fields drop in declaration order: the listener goes before its stream.
    _listener: pw::stream::StreamListener<Rc<RefCell<LoopState>>>,
    stream: pw::stream::StreamRc,
}

fn open_stream(
    core: &pw::core::CoreRc,
    state: &Rc<RefCell<LoopState>>,
) -> Result<ActiveStream, CaptureError> {
    let stream = pw::stream::StreamRc::new(
        core.clone(),
        "pcbridge-capture",
        properties! {
            *pw::keys::MEDIA_TYPE => "Video",
            *pw::keys::MEDIA_CATEGORY => "Capture",
            *pw::keys::MEDIA_ROLE => "Screen",
        },
    )
    .map_err(|error| CaptureError::Unavailable(error.to_string()))?;

    let listener = stream
        .add_local_listener_with_user_data(Rc::clone(state))
        .state_changed(|_, state, _, new| {
            if let pw::stream::StreamState::Error(message) = new {
                state.borrow().attached.store(false, Ordering::Release);
                send_event(
                    &state.borrow().events,
                    WorkerEvent::Error(CaptureError::Stream(message)),
                );
            }
        })
        .param_changed(|_, state, id, param| {
            if id != spa::param::ParamType::Format.as_raw() {
                return;
            }
            let Some(param) = param else {
                state.borrow_mut().format = None;
                return;
            };
            let parsed = parse_format(param);
            match parsed {
                Ok(format) => state.borrow_mut().format = Some(format),
                Err(error) => {
                    state.borrow_mut().format = None;
                    send_event(&state.borrow().events, WorkerEvent::Error(error));
                }
            }
        })
        .process(|stream, state| {
            let received_at = Instant::now();
            let (format, local_sequence, source_timestamp_ns) = {
                let mut state = state.borrow_mut();
                let Some(format) = state.format else {
                    return;
                };
                state.local_sequence = state.local_sequence.saturating_add(1);
                let elapsed = received_at.duration_since(state.clock_origin).as_nanos();
                let timestamp = i64::try_from(elapsed).unwrap_or(i64::MAX);
                (format, state.local_sequence, timestamp)
            };
            let Some(frame) = copy_frame(
                stream,
                format,
                received_at,
                local_sequence,
                source_timestamp_ns,
            ) else {
                return;
            };
            let event = match frame {
                Ok(frame) => WorkerEvent::Frame(frame),
                Err(error) => WorkerEvent::Error(error),
            };
            let event = match stream.disconnect() {
                Ok(()) => {
                    state.borrow().attached.store(false, Ordering::Release);
                    event
                }
                Err(error) => WorkerEvent::Error(CaptureError::Stream(format!(
                    "failed to release the captured stream: {error}"
                ))),
            };
            send_event(&state.borrow().events, event);
        })
        .register()
        .map_err(|error| CaptureError::Unavailable(error.to_string()))?;

    Ok(ActiveStream {
        _listener: listener,
        stream,
    })
}

fn attach_stream(
    core: &pw::core::CoreRc,
    state: &Rc<RefCell<LoopState>>,
    active: &RefCell<Option<ActiveStream>>,
    node: u32,
) -> Result<(), CaptureError> {
    detach_stream(state, active);

    let opened = open_stream(core, state)?;
    let values = format_parameter()?;
    let pod = Pod::from_bytes(&values)
        .ok_or_else(|| CaptureError::Unavailable("invalid SPA format parameter".to_owned()))?;
    let mut params = [pod];
    // Targeting by node id is deprecated in libpipewire, whose header asks for
    // `target.object` set to the node's object.serial. Mutter reports only the
    // id, and a fresh stream per node is what makes the id reliable here: it
    // is the same mechanism the legacy `pipewiresrc path=<id>` has used.
    opened
        .stream
        .connect(
            spa::utils::Direction::Input,
            Some(node),
            pw::stream::StreamFlags::AUTOCONNECT | pw::stream::StreamFlags::MAP_BUFFERS,
            &mut params,
        )
        .map_err(|error| CaptureError::Stream(error.to_string()))?;
    state.borrow().attached.store(true, Ordering::Release);
    *active.borrow_mut() = Some(opened);
    Ok(())
}

fn detach_stream(state: &Rc<RefCell<LoopState>>, active: &RefCell<Option<ActiveStream>>) {
    state.borrow().attached.store(false, Ordering::Release);
    state.borrow_mut().format = None;
    let previous = active.borrow_mut().take();
    if let Some(previous) = previous {
        let _ = previous.stream.disconnect();
    }
}

fn format_parameter() -> Result<Vec<u8>, CaptureError> {
    let object = spa::pod::object!(
        spa::utils::SpaTypes::ObjectParamFormat,
        spa::param::ParamType::EnumFormat,
        spa::pod::property!(
            spa::param::format::FormatProperties::MediaType,
            Id,
            spa::param::format::MediaType::Video
        ),
        spa::pod::property!(
            spa::param::format::FormatProperties::MediaSubtype,
            Id,
            spa::param::format::MediaSubtype::Raw
        ),
        spa::pod::property!(
            spa::param::format::FormatProperties::VideoFormat,
            Choice,
            Enum,
            Id,
            VideoFormat::BGRx,
            VideoFormat::BGRx,
            VideoFormat::RGBx,
            VideoFormat::BGRA,
            VideoFormat::RGBA,
        ),
    );
    spa::pod::serialize::PodSerializer::serialize(
        Cursor::new(Vec::new()),
        &spa::pod::Value::Object(object),
    )
    .map(|(cursor, _)| cursor.into_inner())
    .map_err(|error| {
        CaptureError::Unavailable(format!("SPA format serialization failed: {error:?}"))
    })
}

fn parse_format(param: &Pod) -> Result<NegotiatedFormat, CaptureError> {
    let (media_type, media_subtype) = spa::param::format_utils::parse_format(param)
        .map_err(|error| CaptureError::Stream(format!("invalid SPA format: {error}")))?;
    if media_type != spa::param::format::MediaType::Video
        || media_subtype != spa::param::format::MediaSubtype::Raw
    {
        return Err(CaptureError::Stream(
            "PipeWire negotiated a non-raw-video stream".to_owned(),
        ));
    }

    let mut info = VideoInfoRaw::default();
    info.parse(param)
        .map_err(|error| CaptureError::Stream(format!("invalid raw video format: {error}")))?;
    Ok(NegotiatedFormat {
        format: pixel_format_from_spa(info.format())?,
        width: info.size().width,
        height: info.size().height,
    })
}

fn copy_frame(
    stream: &pw::stream::Stream,
    format: NegotiatedFormat,
    received_at: Instant,
    local_sequence: u64,
    source_timestamp_ns: i64,
) -> Option<Result<SourceFrame, CaptureError>> {
    let mut buffer = stream.dequeue_buffer()?;
    let producer_id = match buffer.find_meta::<MetaHeader>() {
        Some(header) => {
            if header.flags().contains(MetaHeaderFlags::CORRUPTED) {
                return Some(Err(CaptureError::Stream(
                    "frame metadata is corrupted".to_owned(),
                )));
            }
            Some(FrameId {
                sequence: header.seq(),
                captured_at_ns: header.pts(),
            })
        }
        None => None,
    };
    let (id, identity_source) =
        resolve_frame_identity(producer_id, local_sequence, source_timestamp_ns);

    let data = match buffer.datas_mut().first_mut() {
        Some(data) => data,
        None => {
            return Some(Err(CaptureError::Stream(
                "frame has no data plane".to_owned(),
            )));
        }
    };
    let chunk = data.chunk();
    if chunk.flags().contains(ChunkFlags::CORRUPTED) {
        return Some(Err(CaptureError::Stream(
            "frame chunk is corrupted".to_owned(),
        )));
    }
    let stride = match u32::try_from(chunk.stride()) {
        Ok(stride) => stride,
        Err(_) => {
            return Some(Err(CaptureError::Stream(format!(
                "negative frame stride {} is unsupported",
                chunk.stride()
            ))));
        }
    };
    let spec = FrameSpec {
        format: format.format,
        width: format.width,
        height: format.height,
        stride,
        offset: chunk.offset(),
        size: chunk.size(),
    };
    let bytes = match data.data() {
        Some(bytes) => bytes,
        None => {
            return Some(Err(CaptureError::Unavailable(
                "PipeWire buffer is not CPU-mappable".to_owned(),
            )));
        }
    };
    Some(
        RgbaFrame::from_buffer(&spec, bytes, id)
            .map(|frame| SourceFrame {
                frame,
                received_at,
                identity_source,
            })
            .map_err(CaptureError::Frame),
    )
}

/// Select the strongest identity actually supplied by the stream.
///
/// Mutter omits `SPA_META_Header` on the measured GNOME 46 system. The
/// fallback is not presented as producer metadata: callers receive its
/// explicit provenance alongside the source-local sequence and documented
/// source-monotonic timestamp.
#[must_use]
pub const fn resolve_frame_identity(
    producer: Option<FrameId>,
    local_sequence: u64,
    source_timestamp_ns: i64,
) -> (FrameId, FrameIdentitySource) {
    match producer {
        Some(id) => (id, FrameIdentitySource::SpaMetaHeader),
        None => (
            FrameId {
                sequence: local_sequence,
                captured_at_ns: source_timestamp_ns,
            },
            FrameIdentitySource::SourceMonotonicClock,
        ),
    }
}

fn send_event(sender: &SyncSender<WorkerEvent>, event: WorkerEvent) {
    match sender.try_send(event) {
        Ok(()) | Err(TrySendError::Full(_)) | Err(TrySendError::Disconnected(_)) => {}
    }
}
