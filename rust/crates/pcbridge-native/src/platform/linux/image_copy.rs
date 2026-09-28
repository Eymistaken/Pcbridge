//! One fresh output frame through ext-image-copy-capture, inside the helper.
//!
//! Each request owns a new session and its first frame. This avoids the
//! protocol's indefinite wait for damage on later frames. All Wayland waits
//! are bounded and cooperative with the grant watchdog; no external capture
//! process or persistent permission is involved.

use std::collections::HashMap;
use std::fs::OpenOptions;
use std::os::fd::{AsFd, OwnedFd};
use std::os::unix::fs::{FileExt, OpenOptionsExt};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

use async_io::{Async, Timer};
use futures_lite::future;
use pcbridge_core::frame::{FrameId, FrameSpec, MAX_PIXELS, PixelFormat, RgbaFrame};
use wayland_client::protocol::{
    wl_buffer, wl_callback, wl_output, wl_registry, wl_shm, wl_shm_pool,
};
use wayland_client::{Connection, Dispatch, EventQueue, QueueHandle, WEnum};
use wayland_protocols::ext::image_capture_source::v1::client::{
    ext_image_capture_source_v1 as source, ext_output_image_capture_source_manager_v1 as sources,
};
use wayland_protocols::ext::image_copy_capture::v1::client::{
    ext_image_copy_capture_frame_v1 as frame, ext_image_copy_capture_manager_v1 as manager,
    ext_image_copy_capture_session_v1 as session,
};

use super::capture::{CaptureError, FrameIdentitySource, SourceFrame};
use super::session::SessionGuard;
use crate::lifecycle::{FailClosed, LifecycleFailure};

#[derive(Default)]
struct State {
    outputs: HashMap<u32, (wl_output::WlOutput, String)>,
    shm: Option<wl_shm::WlShm>,
    manager: Option<manager::ExtImageCopyCaptureManagerV1>,
    sources: Option<sources::ExtOutputImageCaptureSourceManagerV1>,
    synced: bool,
    constraints: bool,
    width: u32,
    height: u32,
    formats: Vec<wl_shm::Format>,
    transform: Option<wl_output::Transform>,
    ready: bool,
    failure: Option<String>,
}

struct Request {
    connection: Connection,
    queue: EventQueue<State>,
    readiness: Async<OwnedFd>,
    state: State,
    deadline: Instant,
    timeout: Duration,
}

impl Request {
    fn checkpoint(
        &self,
        capture: &ImageCopy,
        generation: u64,
        guard: &dyn SessionGuard,
    ) -> Result<(), CaptureError> {
        if capture.generation.load(Ordering::Acquire) != generation {
            return Err(CaptureError::Canceled);
        }
        guard.check().map_err(CaptureError::Guard)?;
        if Instant::now() >= self.deadline {
            return Err(CaptureError::Timeout(self.timeout));
        }
        Ok(())
    }

    fn wait(
        &mut self,
        capture: &ImageCopy,
        generation: u64,
        guard: &dyn SessionGuard,
        done: impl Fn(&State) -> bool,
    ) -> Result<(), CaptureError> {
        loop {
            self.checkpoint(capture, generation, guard)?;
            self.queue
                .dispatch_pending(&mut self.state)
                .map_err(stream_error)?;
            if let Some(error) = &self.state.failure {
                return Err(CaptureError::Stream(error.clone()));
            }
            if done(&self.state) {
                return Ok(());
            }
            self.connection.flush().map_err(stream_error)?;
            if let Some(read) = self.queue.prepare_read() {
                let delay = self
                    .deadline
                    .saturating_duration_since(Instant::now())
                    .min(Duration::from_millis(25));
                let ready = future::block_on(future::or(
                    async { self.readiness.readable().await.map(|()| true) },
                    async {
                        Timer::after(delay).await;
                        Ok(false)
                    },
                ))
                .map_err(stream_error)?;
                if ready {
                    read.read().map_err(stream_error)?;
                }
            }
        }
    }

    fn sync(
        &mut self,
        capture: &ImageCopy,
        generation: u64,
        guard: &dyn SessionGuard,
    ) -> Result<(), CaptureError> {
        self.state.synced = false;
        self.connection.display().sync(&self.queue.handle(), ());
        self.wait(capture, generation, guard, |state| state.synced)
    }
}

fn stream_error(error: impl std::fmt::Display) -> CaptureError {
    CaptureError::Stream(error.to_string())
}

/// Limits and packed format are decided before allocating a shared file.
pub fn buffer_spec(
    width: u32,
    height: u32,
    formats: &[wl_shm::Format],
) -> Result<(FrameSpec, wl_shm::Format), CaptureError> {
    let pixels = u64::from(width) * u64::from(height);
    if pixels == 0 || pixels > MAX_PIXELS || width > i32::MAX as u32 || height > i32::MAX as u32 {
        return Err(CaptureError::Stream(
            "Image-copy buffer dimensions exceed the frame limit".into(),
        ));
    }
    let size = u32::try_from(pixels * 4).map_err(stream_error)?;
    let stride = width
        .checked_mul(4)
        .ok_or_else(|| stream_error("Image-copy stride overflow"))?;
    let format = [wl_shm::Format::Xrgb8888, wl_shm::Format::Argb8888]
        .into_iter()
        .find(|format| formats.contains(format))
        .ok_or(CaptureError::UnsupportedFormat(u32::MAX))?;
    // A displayed output is opaque; ignore alpha blending residue just as
    // the KWin monitor adapter does. Both formats are B,G,R,x on little endian.
    if !cfg!(target_endian = "little") {
        return Err(CaptureError::Unavailable(
            "Image-copy packed formats require little endian".into(),
        ));
    }
    Ok((
        FrameSpec {
            format: PixelFormat::Bgrx,
            width,
            height,
            stride,
            offset: 0,
            size,
        },
        format,
    ))
}

/// Apply the inverse of the transform the compositor applied to the buffer.
pub fn normalize(
    mut image: RgbaFrame,
    transform: wl_output::Transform,
) -> Result<RgbaFrame, CaptureError> {
    use wl_output::Transform as T;
    let pixels = u64::from(image.width) * u64::from(image.height);
    if pixels == 0 || pixels > MAX_PIXELS || image.pixels.len() as u64 != pixels * 4 {
        return Err(stream_error("Invalid owned image-copy frame dimensions"));
    }
    if transform == T::Normal {
        return Ok(image);
    }
    let (w, h) = (image.width, image.height);
    let (width, height) = match transform {
        T::_90 | T::_270 | T::Flipped90 | T::Flipped270 => (h, w),
        T::_180 | T::Flipped | T::Flipped180 => (w, h),
        _ => return Err(stream_error("Unknown image-copy buffer transform")),
    };
    let mut pixels = vec![0; image.pixels.len()];
    for y in 0..h {
        for x in 0..w {
            let (dx, dy) = match transform {
                T::_90 => (h - 1 - y, x),
                T::_180 => (w - 1 - x, h - 1 - y),
                T::_270 => (y, w - 1 - x),
                T::Flipped => (w - 1 - x, y),
                T::Flipped90 => (y, x),
                T::Flipped180 => (x, h - 1 - y),
                T::Flipped270 => (h - 1 - y, w - 1 - x),
                _ => unreachable!(),
            };
            let from = ((y * w + x) * 4) as usize;
            let to = ((dy * width + dx) * 4) as usize;
            pixels[to..to + 4].copy_from_slice(&image.pixels[from..from + 4]);
        }
    }
    image.width = width;
    image.height = height;
    image.pixels = pixels;
    Ok(image)
}

#[derive(Debug)]
pub struct ImageCopy {
    started: Instant,
    sequence: AtomicU64,
    generation: AtomicU64,
}

impl Default for ImageCopy {
    fn default() -> Self {
        Self {
            started: Instant::now(),
            sequence: AtomicU64::new(0),
            generation: AtomicU64::new(0),
        }
    }
}

impl FailClosed for ImageCopy {
    fn close_fail_closed(&self, _: LifecycleFailure) {
        self.generation.fetch_add(1, Ordering::AcqRel);
    }
}

impl ImageCopy {
    /// Read-only registry discovery. No image source/session or buffer is
    /// created, so capability queries never capture pixels or need a grant.
    pub fn probe() -> Result<(), CaptureError> {
        struct Discovery;
        impl SessionGuard for Discovery {
            fn check(&self) -> Result<(), LifecycleFailure> {
                Ok(())
            }
        }
        let timeout = Duration::from_millis(500);
        let connection = Connection::connect_to_env()
            .map_err(|error| CaptureError::Unavailable(error.to_string()))?;
        let queue = connection.new_event_queue::<State>();
        let fd = connection
            .as_fd()
            .try_clone_to_owned()
            .map_err(stream_error)?;
        let mut request = Request {
            connection,
            queue,
            readiness: Async::new(fd).map_err(stream_error)?,
            state: State::default(),
            deadline: Instant::now() + timeout,
            timeout,
        };
        let _registry = request
            .connection
            .display()
            .get_registry(&request.queue.handle(), ());
        request.sync(&Self::default(), 0, &Discovery)?;
        if request.state.shm.is_none()
            || request.state.manager.is_none()
            || request.state.sources.is_none()
        {
            return Err(CaptureError::Unavailable(
                "The selected Wayland compositor lacks the image-copy/output-source/SHM protocols"
                    .into(),
            ));
        }
        Ok(())
    }

    pub fn capture(
        &self,
        connector: &str,
        include_pointer: bool,
        timeout: Duration,
        guard: &dyn SessionGuard,
    ) -> Result<SourceFrame, CaptureError> {
        let requested = Instant::now();
        let generation = self.generation.load(Ordering::Acquire);
        guard.check().map_err(CaptureError::Guard)?;
        let connection = Connection::connect_to_env()
            .map_err(|error| CaptureError::Unavailable(error.to_string()))?;
        let queue = connection.new_event_queue::<State>();
        let fd = connection
            .as_fd()
            .try_clone_to_owned()
            .map_err(stream_error)?;
        let mut request = Request {
            connection,
            queue,
            readiness: Async::new(fd).map_err(stream_error)?,
            state: State::default(),
            deadline: requested + timeout,
            timeout,
        };
        let qh = request.queue.handle();
        let _registry = request.connection.display().get_registry(&qh, ());
        request.sync(self, generation, guard)?;
        // Registry dispatch bound the output globals; the next sync includes
        // their names, never a guessed enumeration index.
        request.sync(self, generation, guard)?;
        let shm = request
            .state
            .shm
            .clone()
            .ok_or_else(|| CaptureError::Unavailable("wl_shm unavailable".into()))?;
        let manager = request.state.manager.clone().ok_or_else(|| {
            CaptureError::Unavailable("ext-image-copy-capture v1 unavailable".into())
        })?;
        let sources = request.state.sources.clone().ok_or_else(|| {
            CaptureError::Unavailable("ext-output-image-capture-source v1 unavailable".into())
        })?;
        let outputs: Vec<_> = request
            .state
            .outputs
            .values()
            .filter(|(_, name)| name == connector)
            .collect();
        if outputs.len() != 1 {
            return Err(stream_error(
                "Output name does not match the selected monitor",
            ));
        }
        request.checkpoint(self, generation, guard)?;
        let source = sources.create_source(&outputs[0].0, &qh, ());
        let options = if include_pointer {
            manager::Options::PaintCursors
        } else {
            manager::Options::empty()
        };
        let session = manager.create_session(&source, options, &qh, ());
        request.wait(self, generation, guard, |state| state.constraints)?;
        let (spec, format) = buffer_spec(
            request.state.width,
            request.state.height,
            &request.state.formats,
        )?;
        let runtime = std::env::var_os("XDG_RUNTIME_DIR")
            .ok_or_else(|| stream_error("No runtime directory"))?;
        let path = std::path::PathBuf::from(runtime).join(format!(
            "pcbridge-capture-{}-{}.shm",
            std::process::id(),
            self.sequence.fetch_add(1, Ordering::AcqRel)
        ));
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&path)
            .map_err(stream_error)?;
        std::fs::remove_file(&path).map_err(stream_error)?;
        file.set_len(u64::from(spec.size)).map_err(stream_error)?;
        let pool = shm.create_pool(file.as_fd(), spec.size as i32, &qh, ());
        let buffer = pool.create_buffer(
            0,
            spec.width as i32,
            spec.height as i32,
            spec.stride as i32,
            format,
            &qh,
            (),
        );
        pool.destroy();
        request.checkpoint(self, generation, guard)?;
        let frame = session.create_frame(&qh, ());
        frame.attach_buffer(&buffer);
        frame.damage_buffer(0, 0, spec.width as i32, spec.height as i32);
        frame.capture();
        request.wait(self, generation, guard, |state| state.ready)?;
        request.checkpoint(self, generation, guard)?;
        let received_at = Instant::now();
        let transform = request
            .state
            .transform
            .ok_or_else(|| stream_error("Image-copy frame has no transform metadata"))?;
        let mut bytes = vec![0; spec.size as usize];
        file.read_exact_at(&mut bytes, 0).map_err(stream_error)?;
        let id = FrameId {
            sequence: self.sequence.load(Ordering::Acquire),
            captured_at_ns: i64::try_from(self.started.elapsed().as_nanos()).unwrap_or(i64::MAX),
        };
        let image = normalize(RgbaFrame::from_buffer(&spec, &bytes, id)?, transform)?;
        frame.destroy();
        buffer.destroy();
        session.destroy();
        source.destroy();
        // Release the compositor connection and its shared file before encode.
        drop(file);
        drop(request);
        if self.generation.load(Ordering::Acquire) != generation {
            return Err(CaptureError::Canceled);
        }
        guard.check().map_err(CaptureError::Guard)?;
        Ok(SourceFrame {
            frame: image,
            received_at,
            identity_source: FrameIdentitySource::SourceMonotonicClock,
        })
    }
}

impl Dispatch<wl_registry::WlRegistry, ()> for State {
    fn event(
        state: &mut Self,
        registry: &wl_registry::WlRegistry,
        event: wl_registry::Event,
        _: &(),
        _: &Connection,
        qh: &QueueHandle<Self>,
    ) {
        match event {
            wl_registry::Event::Global {
                name,
                interface,
                version,
            } => match interface.as_str() {
                "wl_shm" => state.shm = Some(registry.bind(name, version.min(1), qh, ())),
                "wl_output" if version >= 4 => {
                    state
                        .outputs
                        .insert(name, (registry.bind(name, 4, qh, name), String::new()));
                }
                "ext_image_copy_capture_manager_v1" => {
                    state.manager = Some(registry.bind(name, 1, qh, ()))
                }
                "ext_output_image_capture_source_manager_v1" => {
                    state.sources = Some(registry.bind(name, 1, qh, ()))
                }
                _ => {}
            },
            wl_registry::Event::GlobalRemove { name } if state.outputs.remove(&name).is_some() => {
                state.failure = Some("An output was removed during capture".into());
            }
            _ => {}
        }
    }
}

impl Dispatch<wl_output::WlOutput, u32> for State {
    fn event(
        state: &mut Self,
        _: &wl_output::WlOutput,
        event: wl_output::Event,
        id: &u32,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let wl_output::Event::Name { name } = event {
            if let Some(output) = state.outputs.get_mut(id) {
                output.1 = name;
            }
        }
    }
}

impl Dispatch<wl_callback::WlCallback, ()> for State {
    fn event(
        state: &mut Self,
        _: &wl_callback::WlCallback,
        _: wl_callback::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        state.synced = true;
    }
}

impl Dispatch<session::ExtImageCopyCaptureSessionV1, ()> for State {
    fn event(
        state: &mut Self,
        _: &session::ExtImageCopyCaptureSessionV1,
        event: session::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        match event {
            session::Event::BufferSize { width, height } => {
                state.width = width;
                state.height = height;
            }
            session::Event::ShmFormat {
                format: WEnum::Value(format),
            } => {
                if state.formats.len() < 64 {
                    state.formats.push(format);
                }
            }
            session::Event::Done => {
                if state.constraints {
                    state.failure = Some("Image-copy constraints changed during capture".into());
                }
                state.constraints = true;
            }
            session::Event::Stopped => state.failure = Some("Image-copy session stopped".into()),
            _ => {}
        }
    }
}

impl Dispatch<frame::ExtImageCopyCaptureFrameV1, ()> for State {
    fn event(
        state: &mut Self,
        _: &frame::ExtImageCopyCaptureFrameV1,
        event: frame::Event,
        _: &(),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        match event {
            frame::Event::Transform {
                transform: WEnum::Value(transform),
            } => state.transform = Some(transform),
            frame::Event::Ready => state.ready = true,
            frame::Event::Failed { reason } => {
                state.failure = Some(format!("Image-copy frame failed: {reason:?}"))
            }
            _ => {}
        }
    }
}

wayland_client::delegate_noop!(State: ignore wl_shm::WlShm);
wayland_client::delegate_noop!(State: ignore wl_shm_pool::WlShmPool);
wayland_client::delegate_noop!(State: ignore wl_buffer::WlBuffer);
wayland_client::delegate_noop!(State: ignore manager::ExtImageCopyCaptureManagerV1);
wayland_client::delegate_noop!(State: ignore sources::ExtOutputImageCaptureSourceManagerV1);
wayland_client::delegate_noop!(State: ignore source::ExtImageCaptureSourceV1);

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::net::UnixStream;
    use std::sync::Arc;

    struct KnownGrant;
    impl SessionGuard for KnownGrant {
        fn check(&self) -> Result<(), LifecycleFailure> {
            Ok(())
        }
    }

    fn silent_peer(timeout: Duration) -> (Request, UnixStream) {
        let (socket, peer) = UnixStream::pair().unwrap();
        let connection = Connection::from_socket(socket).unwrap();
        let queue = connection.new_event_queue();
        let fd = connection.as_fd().try_clone_to_owned().unwrap();
        (
            Request {
                connection,
                queue,
                readiness: Async::new(fd).unwrap(),
                state: State::default(),
                deadline: Instant::now() + timeout,
                timeout,
            },
            peer,
        )
    }

    #[test]
    fn a_stalled_wayland_peer_cannot_hold_capture_past_its_deadline() {
        let (mut request, _peer) = silent_peer(Duration::from_millis(100));
        let started = Instant::now();
        assert!(matches!(
            request.sync(&ImageCopy::default(), 0, &KnownGrant),
            Err(CaptureError::Timeout(_))
        ));
        assert!(started.elapsed() < Duration::from_millis(200));
    }

    #[test]
    fn watchdog_cancellation_ends_an_inflight_wayland_wait() {
        let (mut request, _peer) = silent_peer(Duration::from_secs(5));
        let capture = Arc::new(ImageCopy::default());
        let closer = capture.clone();
        let watchdog = std::thread::spawn(move || {
            std::thread::sleep(Duration::from_millis(50));
            closer.close_fail_closed(LifecycleFailure::Revoked);
        });
        let started = Instant::now();
        assert!(matches!(
            request.sync(&capture, 0, &KnownGrant),
            Err(CaptureError::Canceled)
        ));
        assert!(started.elapsed() < Duration::from_millis(150));
        watchdog.join().unwrap();
    }
}
