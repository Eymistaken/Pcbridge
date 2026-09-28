//! Input-transparent white edge strips, matching the GNOME reference frame.
//!
//! This renderer grants no control. Its owner must bind presentation health to
//! a lease before enabling protected operations. No seat or input object is
//! bound, and every surface has an explicit empty input region.

mod appearance;

pub use appearance::{
    BREATH_HALF_MS, EDGE_ALPHA, FADE_IN_MS, FADE_OUT_MS, FALLOFF, breath, depth, fade_in, fade_out,
    falloff,
};
use appearance::{Edge, pixels};

use std::collections::HashMap;
use std::fs::{File, OpenOptions};
use std::os::fd::{AsFd, OwnedFd};
use std::os::unix::fs::{FileExt, OpenOptionsExt};
use std::time::{Duration, Instant};

use async_io::{Async, Timer};
use futures_lite::future;
use pcbridge_core::display::Monitor;
use wayland_client::globals::{GlobalListContents, registry_queue_init};
use wayland_client::protocol::{
    wl_buffer, wl_compositor, wl_output, wl_region, wl_registry, wl_shm, wl_shm_pool, wl_surface,
};
use wayland_client::{Connection, Dispatch, EventQueue, QueueHandle};
use wayland_protocols::wp::presentation_time::client::{wp_presentation, wp_presentation_feedback};
use wayland_protocols_wlr::layer_shell::v1::client::{zwlr_layer_shell_v1, zwlr_layer_surface_v1};

const MAX_POOL_BYTES: u64 = 16 * 1024 * 1024;
const MAX_TOTAL_BYTES: u64 = 64 * 1024 * 1024;
const PRESENTATION_MAX_AGE: Duration = Duration::from_millis(1200);

impl Edge {
    fn anchor(self) -> zwlr_layer_surface_v1::Anchor {
        use zwlr_layer_surface_v1::Anchor as A;
        match self {
            Self::Top => A::Top | A::Left | A::Right,
            Self::Bottom => A::Bottom | A::Left | A::Right,
            Self::Left => A::Left | A::Top | A::Bottom,
            Self::Right => A::Right | A::Top | A::Bottom,
        }
    }
}

#[derive(Clone)]
struct OutputData {
    global: u32,
}

struct Output {
    proxy: wl_output::WlOutput,
    name: String,
}

struct Strip {
    surface: wl_surface::WlSurface,
    layer: zwlr_layer_surface_v1::ZwlrLayerSurfaceV1,
    file: File,
    buffers: [wl_buffer::WlBuffer; 2],
    available: [bool; 2],
    edge: Edge,
    width: u32,
    height: u32,
    scale: u32,
    configured: bool,
    last_presented: Option<(Instant, f64)>,
}

#[derive(Clone, Copy)]
struct Feedback {
    strip: usize,
    opacity: f64,
}

#[derive(Default)]
struct State {
    outputs: HashMap<u32, Output>,
    strips: Vec<Strip>,
    initialized: bool,
    failure: Option<String>,
}

pub struct Overlay {
    connection: Connection,
    queue: EventQueue<State>,
    readiness: Async<OwnedFd>,
    presentation: wp_presentation::WpPresentation,
    state: State,
}

fn shared_file(index: usize) -> Result<File, String> {
    let runtime = std::env::var_os("XDG_RUNTIME_DIR").ok_or("XDG_RUNTIME_DIR unavailable")?;
    let path = std::path::PathBuf::from(runtime)
        .join(format!("pcbridge-glow-{}-{index}.shm", std::process::id()));
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&path)
        .map_err(|e| e.to_string())?;
    // Only the open descriptor crosses to Wayland; no persistent shared file.
    std::fs::remove_file(path).map_err(|e| e.to_string())?;
    Ok(file)
}

impl Overlay {
    pub fn connect(monitors: &[Monitor]) -> Result<Self, String> {
        if monitors.is_empty() || super::desktop::hyprland_socket().is_none() {
            return Err("No selected Hyprland output table".into());
        }
        let connection = Connection::connect_to_env().map_err(|e| e.to_string())?;
        let (globals, mut queue) =
            registry_queue_init::<State>(&connection).map_err(|e| e.to_string())?;
        let handle = queue.handle();
        let compositor: wl_compositor::WlCompositor = globals
            .bind(&handle, 4..=4, ())
            .map_err(|e| e.to_string())?;
        let shm: wl_shm::WlShm = globals
            .bind(&handle, 1..=1, ())
            .map_err(|e| e.to_string())?;
        let shell: zwlr_layer_shell_v1::ZwlrLayerShellV1 = globals
            .bind(&handle, 3..=5, ())
            .map_err(|e| e.to_string())?;
        let presentation: wp_presentation::WpPresentation = globals
            .bind(&handle, 1..=1, ())
            .map_err(|e| e.to_string())?;
        let mut state = State::default();
        globals.contents().with_list(|list| {
            for global in list
                .iter()
                .filter(|g| g.interface == "wl_output" && g.version >= 4)
            {
                let proxy = globals.registry().bind(
                    global.name,
                    4,
                    &handle,
                    OutputData {
                        global: global.name,
                    },
                );
                state.outputs.insert(
                    global.name,
                    Output {
                        proxy,
                        name: String::new(),
                    },
                );
            }
        });
        queue.roundtrip(&mut state).map_err(|e| e.to_string())?;
        let mut total_bytes = 0_u64;
        for monitor in monitors {
            let matches: Vec<_> = state
                .outputs
                .values()
                .filter(|o| o.name == monitor.connector)
                .collect();
            if matches.len() != 1 {
                return Err("Output names do not match the selected monitor table".into());
            }
            let output = matches[0].proxy.clone();
            if !monitor.scale.is_finite() || monitor.scale <= 0.0 || monitor.scale > 8.0 {
                return Err("Unsupported output buffer scale".into());
            }
            let scale = monitor.scale.ceil().max(1.0) as u32;
            let depth = depth(monitor.width, monitor.height);
            for edge in [Edge::Top, Edge::Bottom, Edge::Left, Edge::Right] {
                let width = if edge.horizontal() {
                    monitor.width
                } else {
                    depth
                };
                let height = if edge.horizontal() {
                    depth
                } else {
                    monitor.height
                };
                let bytes = u64::from(width) * u64::from(height) * u64::from(scale).pow(2) * 4 * 2;
                total_bytes = total_bytes
                    .checked_add(bytes)
                    .ok_or("Overlay allocation overflow")?;
                if bytes > MAX_POOL_BYTES || total_bytes > MAX_TOTAL_BYTES {
                    return Err("Overlay buffer limit exceeded".into());
                }
                let index = state.strips.len();
                let file = shared_file(index)?;
                file.set_len(bytes).map_err(|e| e.to_string())?;
                let pool = shm.create_pool(file.as_fd(), bytes as i32, &handle, ());
                let make = |slot| {
                    pool.create_buffer(
                        slot * (bytes / 2) as i32,
                        (width * scale) as i32,
                        (height * scale) as i32,
                        (width * scale * 4) as i32,
                        wl_shm::Format::Argb8888,
                        &handle,
                        (index, slot as usize),
                    )
                };
                let buffers = [make(0), make(1)];
                pool.destroy();
                let surface = compositor.create_surface(&handle, ());
                let region = compositor.create_region(&handle, ());
                surface.set_input_region(Some(&region));
                region.destroy();
                surface.set_buffer_scale(scale as i32);
                let layer = shell.get_layer_surface(
                    &surface,
                    Some(&output),
                    zwlr_layer_shell_v1::Layer::Overlay,
                    "pcbridge-glow".into(),
                    &handle,
                    index,
                );
                layer
                    .set_keyboard_interactivity(zwlr_layer_surface_v1::KeyboardInteractivity::None);
                // -1 reserves no space and keeps strips at the physical edge,
                // even when a panel has a positive exclusive zone. Zero would
                // shift the glow inward around that panel.
                layer.set_exclusive_zone(-1);
                layer.set_anchor(edge.anchor());
                layer.set_size(width, height);
                surface.commit();
                state.strips.push(Strip {
                    surface,
                    layer,
                    file,
                    buffers,
                    available: [true, true],
                    edge,
                    width,
                    height,
                    scale,
                    configured: false,
                    last_presented: None,
                });
            }
        }
        state.initialized = true;
        let readiness = Async::new(
            connection
                .as_fd()
                .try_clone_to_owned()
                .map_err(|e| e.to_string())?,
        )
        .map_err(|e| e.to_string())?;
        Ok(Self {
            connection,
            queue,
            readiness,
            presentation,
            state,
        })
    }

    pub fn draw(&mut self, opacity: f64, breathing: f64) -> Result<(), String> {
        if let Some(failure) = &self.state.failure {
            return Err(failure.clone());
        }
        if !opacity.is_finite()
            || !(0.0..=1.0).contains(&opacity)
            || !breathing.is_finite()
            || !(0.88..=1.0).contains(&breathing)
        {
            return Err("Invalid glow animation state".into());
        }
        let handle = self.queue.handle();
        for (index, strip) in self.state.strips.iter_mut().enumerate() {
            if !strip.configured {
                continue;
            }
            let Some(slot) = strip.available.iter().position(|free| *free) else {
                continue;
            };
            let width = strip.width * strip.scale;
            let height = strip.height * strip.scale;
            let bytes = pixels(strip.edge, width, height, opacity, breathing);
            strip
                .file
                .write_all_at(&bytes, (slot * bytes.len()) as u64)
                .map_err(|e| e.to_string())?;
            strip.available[slot] = false;
            self.presentation.feedback(
                &strip.surface,
                &handle,
                Feedback {
                    strip: index,
                    opacity,
                },
            );
            strip.surface.attach(Some(&strip.buffers[slot]), 0, 0);
            strip
                .surface
                .damage_buffer(0, 0, width as i32, height as i32);
            strip.surface.commit();
        }
        self.connection.flush().map_err(|e| e.to_string())
    }

    pub fn poll(&mut self, timeout: Duration) -> Result<(), String> {
        self.queue
            .dispatch_pending(&mut self.state)
            .map_err(|e| e.to_string())?;
        self.connection.flush().map_err(|e| e.to_string())?;
        if let Some(read) = self.queue.prepare_read() {
            let ready = future::block_on(future::or(
                async { self.readiness.readable().await.map(|()| true) },
                async {
                    Timer::after(timeout).await;
                    Ok(false)
                },
            ))
            .map_err(|e| e.to_string())?;
            if ready {
                read.read().map_err(|e| e.to_string())?;
            } else {
                drop(read);
            }
            self.queue
                .dispatch_pending(&mut self.state)
                .map_err(|e| e.to_string())?;
        }
        self.state.failure.clone().map_or(Ok(()), Err)
    }

    /// A compositor presentation event for every fully visible strip. A
    /// configure ACK or socket sync alone is not proof of displayed content.
    #[must_use]
    pub fn presented(&self) -> bool {
        !self.state.strips.is_empty()
            && self.state.failure.is_none()
            && self.state.strips.iter().all(|strip| {
                strip.last_presented.is_some_and(|(at, opacity)| {
                    at.elapsed() <= PRESENTATION_MAX_AGE && opacity >= 0.99
                })
            })
    }

    #[must_use]
    pub fn strip_count(&self) -> usize {
        self.state.strips.len()
    }
}

impl Drop for Overlay {
    fn drop(&mut self) {
        for strip in &self.state.strips {
            strip.layer.destroy();
            strip.surface.destroy();
            for buffer in &strip.buffers {
                buffer.destroy();
            }
        }
        let _ = self.connection.flush();
    }
}

impl Dispatch<wl_registry::WlRegistry, GlobalListContents> for State {
    fn event(
        state: &mut Self,
        _: &wl_registry::WlRegistry,
        event: wl_registry::Event,
        _: &GlobalListContents,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if state.initialized
            && match event {
                wl_registry::Event::Global { interface, .. } => interface == "wl_output",
                wl_registry::Event::GlobalRemove { name } => state.outputs.contains_key(&name),
                _ => false,
            }
        {
            state.failure = Some("Output topology changed".into());
        }
    }
}

impl Dispatch<wl_output::WlOutput, OutputData> for State {
    fn event(
        state: &mut Self,
        _: &wl_output::WlOutput,
        event: wl_output::Event,
        data: &OutputData,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if let wl_output::Event::Name { name } = event
            && let Some(output) = state.outputs.get_mut(&data.global)
        {
            output.name = name;
        }
    }
}

impl Dispatch<zwlr_layer_surface_v1::ZwlrLayerSurfaceV1, usize> for State {
    fn event(
        state: &mut Self,
        layer: &zwlr_layer_surface_v1::ZwlrLayerSurfaceV1,
        event: zwlr_layer_surface_v1::Event,
        index: &usize,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        match event {
            zwlr_layer_surface_v1::Event::Configure {
                serial,
                width,
                height,
            } => {
                let strip = &mut state.strips[*index];
                layer.ack_configure(serial);
                if width != strip.width || height != strip.height {
                    state.failure = Some("Layer geometry does not match its output edge".into());
                } else {
                    strip.configured = true;
                }
            }
            zwlr_layer_surface_v1::Event::Closed => {
                state.failure = Some("Glow layer was closed".into())
            }
            _ => {}
        }
    }
}

impl Dispatch<wl_buffer::WlBuffer, (usize, usize)> for State {
    fn event(
        state: &mut Self,
        _: &wl_buffer::WlBuffer,
        event: wl_buffer::Event,
        data: &(usize, usize),
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if matches!(event, wl_buffer::Event::Release) {
            state.strips[data.0].available[data.1] = true;
        }
    }
}

impl Dispatch<wp_presentation_feedback::WpPresentationFeedback, Feedback> for State {
    fn event(
        state: &mut Self,
        _: &wp_presentation_feedback::WpPresentationFeedback,
        event: wp_presentation_feedback::Event,
        data: &Feedback,
        _: &Connection,
        _: &QueueHandle<Self>,
    ) {
        if matches!(event, wp_presentation_feedback::Event::Presented { .. }) {
            state.strips[data.strip].last_presented = Some((Instant::now(), data.opacity));
        }
    }
}

wayland_client::delegate_noop!(State: ignore wl_compositor::WlCompositor);
wayland_client::delegate_noop!(State: ignore wl_shm::WlShm);
wayland_client::delegate_noop!(State: ignore wl_shm_pool::WlShmPool);
wayland_client::delegate_noop!(State: ignore wl_surface::WlSurface);
wayland_client::delegate_noop!(State: ignore wl_region::WlRegion);
wayland_client::delegate_noop!(State: ignore zwlr_layer_shell_v1::ZwlrLayerShellV1);
wayland_client::delegate_noop!(State: ignore wp_presentation::WpPresentation);
