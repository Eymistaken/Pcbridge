//! The grant frame on X11 (Cinnamon, Xfce): the same white edge strips as
//! the GNOME extension and the Hyprland renderer, as override-redirect
//! windows.
//!
//! Each strip is a window the window manager does not manage
//! (override-redirect), with an empty input shape, so every click goes to the
//! application below. With a compositing manager (Muffin always composites;
//! xfwm4 does by default) the strips are 32-bit ARGB windows and carry the
//! breathing gradient; without one they fall back to thin opaque bars, the
//! only visible frame an uncomposited X server can show.
//!
//! X11 has no presentation feedback. The proof here is weaker than
//! Hyprland's: every strip is read back from the server as mapped, viewable
//! and at its place, and the frame is raised over other windows on every
//! draw. Any client of the display could unmap or cover it; the frame owner
//! then reports no presentation and control pauses (`docs/security.md`).

use std::time::{Duration, Instant};

use pcbridge_core::display::Monitor;
use x11rb::connection::{Connection, RequestConnection};
use x11rb::protocol::Event;
use x11rb::protocol::shape::{self, ConnectionExt as _};
use x11rb::protocol::xproto::{
    AtomEnum, ClipOrdering, ColormapAlloc, ConfigureWindowAux, ConnectionExt as _, CreateGCAux,
    CreateWindowAux, EventMask, ImageFormat, MapState, PropMode, StackMode, VisualClass, Window,
    WindowClass,
};
use x11rb::wrapper::ConnectionExt as _;

use super::appearance::{Edge, depth, pixels};
use crate::platform::linux::x11::X11;

/// The bar width without a compositing manager, in pixels.
const SOLID_DEPTH: u32 = 4;
/// The bars' color without a compositing manager: near white.
const SOLID_PIXEL: u32 = 0x00F0_F0F0;
/// How often the strips are read back from the server.
const VERIFY_EVERY: Duration = Duration::from_millis(200);
/// How long `connect` waits for the strips to be mapped.
const MAP_TIMEOUT: Duration = Duration::from_secs(2);

struct Strip {
    window: Window,
    gc: u32,
    edge: Edge,
    rect: (i16, i16, u16, u16),
}

pub struct X11Overlay {
    x11: X11,
    composited: bool,
    strips: Vec<Strip>,
    /// (opacity, breathing) of the last upload, in hundredths.
    drawn: Option<(i64, i64)>,
    visible: bool,
    verified_at: Option<Instant>,
    serial: u64,
}

impl X11Overlay {
    pub fn connect(monitors: &[Monitor]) -> Result<Self, String> {
        if monitors.is_empty() {
            return Err("No X11 monitor table".into());
        }
        let x11 = X11::connect().map_err(|error| error.to_string())?;
        let connection = x11.connection();
        let screen = &connection.setup().roots[0];
        let root = x11.root();
        let manager = connection
            .intern_atom(false, b"_NET_WM_CM_S0")
            .map_err(|e| e.to_string())?
            .reply()
            .map_err(|e| e.to_string())?
            .atom;
        let composited = connection
            .get_selection_owner(manager)
            .map_err(|e| e.to_string())?
            .reply()
            .map_err(|e| e.to_string())?
            .owner
            != x11rb::NONE;
        let argb = screen
            .allowed_depths
            .iter()
            .filter(|allowed| allowed.depth == 32)
            .flat_map(|allowed| allowed.visuals.iter())
            .find(|visual| visual.class == VisualClass::TRUE_COLOR)
            .map(|visual| visual.visual_id);
        let (window_depth, visual, colormap) = match (composited, argb) {
            (true, Some(visual)) => {
                let colormap = connection.generate_id().map_err(|e| e.to_string())?;
                connection
                    .create_colormap(ColormapAlloc::NONE, colormap, root, visual)
                    .map_err(|e| e.to_string())?;
                (32, visual, colormap)
            }
            _ => (
                screen.root_depth,
                screen.root_visual,
                screen.default_colormap,
            ),
        };
        let composited = composited && argb.is_some();
        let names = [
            connection.intern_atom(false, b"_NET_WM_WINDOW_TYPE"),
            connection.intern_atom(false, b"_NET_WM_WINDOW_TYPE_NOTIFICATION"),
            connection.intern_atom(false, b"UTF8_STRING"),
            connection.intern_atom(false, b"_NET_WM_NAME"),
        ];
        let mut atoms = Vec::new();
        for cookie in names {
            atoms.push(
                cookie
                    .map_err(|e| e.to_string())?
                    .reply()
                    .map_err(|e| e.to_string())?
                    .atom,
            );
        }
        let mut strips = Vec::new();
        for monitor in monitors {
            let thickness = if composited {
                depth(monitor.width, monitor.height)
            } else {
                SOLID_DEPTH
            };
            for edge in [Edge::Top, Edge::Bottom, Edge::Left, Edge::Right] {
                let (x, y, width, height) = match edge {
                    Edge::Top => (monitor.x, monitor.y, monitor.width, thickness),
                    Edge::Bottom => (
                        monitor.x,
                        monitor.y + i32::try_from(monitor.height - thickness).unwrap_or(0),
                        monitor.width,
                        thickness,
                    ),
                    Edge::Left => (monitor.x, monitor.y, thickness, monitor.height),
                    Edge::Right => (
                        monitor.x + i32::try_from(monitor.width - thickness).unwrap_or(0),
                        monitor.y,
                        thickness,
                        monitor.height,
                    ),
                };
                let rect = (
                    i16::try_from(x).map_err(|_| "Monitor outside the X11 range")?,
                    i16::try_from(y).map_err(|_| "Monitor outside the X11 range")?,
                    u16::try_from(width).map_err(|_| "Monitor outside the X11 range")?,
                    u16::try_from(height).map_err(|_| "Monitor outside the X11 range")?,
                );
                let window = connection.generate_id().map_err(|e| e.to_string())?;
                let aux = CreateWindowAux::new()
                    .background_pixel(if composited { 0 } else { SOLID_PIXEL })
                    .border_pixel(0)
                    .override_redirect(1)
                    .colormap(colormap)
                    .event_mask(EventMask::STRUCTURE_NOTIFY);
                connection
                    .create_window(
                        window_depth,
                        window,
                        root,
                        rect.0,
                        rect.1,
                        rect.2,
                        rect.3,
                        0,
                        WindowClass::INPUT_OUTPUT,
                        visual,
                        &aux,
                    )
                    .map_err(|e| e.to_string())?;
                // An empty input shape: the strip never takes a click.
                connection
                    .shape_rectangles(
                        shape::SO::SET,
                        shape::SK::INPUT,
                        ClipOrdering::UNSORTED,
                        window,
                        0,
                        0,
                        &[],
                    )
                    .map_err(|e| e.to_string())?;
                connection
                    .change_property32(
                        PropMode::REPLACE,
                        window,
                        atoms[0],
                        AtomEnum::ATOM,
                        &[atoms[1]],
                    )
                    .map_err(|e| e.to_string())?;
                connection
                    .change_property8(
                        PropMode::REPLACE,
                        window,
                        atoms[3],
                        atoms[2],
                        b"pcbridge desktop grant",
                    )
                    .map_err(|e| e.to_string())?;
                connection
                    .change_property8(
                        PropMode::REPLACE,
                        window,
                        AtomEnum::WM_CLASS,
                        AtomEnum::STRING,
                        b"pcbridge-frame\0pcbridge\0",
                    )
                    .map_err(|e| e.to_string())?;
                let gc = connection.generate_id().map_err(|e| e.to_string())?;
                connection
                    .create_gc(gc, window, &CreateGCAux::new())
                    .map_err(|e| e.to_string())?;
                strips.push(Strip {
                    window,
                    gc,
                    edge,
                    rect,
                });
            }
        }
        let mut overlay = Self {
            x11,
            composited,
            strips,
            drawn: None,
            visible: false,
            verified_at: None,
            serial: 0,
        };
        overlay.set_visible(true)?;
        overlay.await_mapped()?;
        Ok(overlay)
    }

    fn set_visible(&mut self, visible: bool) -> Result<(), String> {
        let connection = self.x11.connection();
        for strip in &self.strips {
            if visible {
                connection
                    .map_window(strip.window)
                    .map_err(|e| e.to_string())?;
            } else {
                connection
                    .unmap_window(strip.window)
                    .map_err(|e| e.to_string())?;
            }
        }
        connection.flush().map_err(|e| e.to_string())?;
        self.visible = visible;
        self.verified_at = None;
        Ok(())
    }

    fn await_mapped(&mut self) -> Result<(), String> {
        let deadline = Instant::now() + MAP_TIMEOUT;
        while Instant::now() < deadline {
            self.drain_events()?;
            if self.verify()? {
                return Ok(());
            }
            std::thread::sleep(Duration::from_millis(10));
        }
        Err("The X11 frame windows were not mapped".into())
    }

    /// Read every strip back: mapped, viewable, and where it was put.
    fn verify(&mut self) -> Result<bool, String> {
        let connection = self.x11.connection();
        for strip in &self.strips {
            let attributes = connection
                .get_window_attributes(strip.window)
                .map_err(|e| e.to_string())?
                .reply()
                .map_err(|e| e.to_string())?;
            if attributes.map_state != MapState::VIEWABLE {
                self.verified_at = None;
                return Ok(false);
            }
            let geometry = connection
                .get_geometry(strip.window)
                .map_err(|e| e.to_string())?
                .reply()
                .map_err(|e| e.to_string())?;
            if (geometry.x, geometry.y, geometry.width, geometry.height) != strip.rect {
                return Err("An X11 frame window was moved".into());
            }
        }
        self.verified_at = Some(Instant::now());
        self.serial += 1;
        Ok(true)
    }

    fn drain_events(&mut self) -> Result<(), String> {
        let connection = self.x11.connection();
        while let Some(event) = connection.poll_for_event().map_err(|e| e.to_string())? {
            match event {
                Event::Error(error) => {
                    return Err(format!("The X server refused a frame request: {error:?}"));
                }
                Event::DestroyNotify(event)
                    if self.strips.iter().any(|strip| strip.window == event.window) =>
                {
                    return Err("An X11 frame window was destroyed".into());
                }
                Event::UnmapNotify(event)
                    if self.visible
                        && self.strips.iter().any(|strip| strip.window == event.window) =>
                {
                    return Err("An X11 frame window was unmapped by another client".into());
                }
                _ => {}
            }
        }
        Ok(())
    }

    pub fn draw(&mut self, opacity: f64, breathing: f64) -> Result<(), String> {
        let shown = opacity > 0.01;
        if !self.composited {
            // Bars cannot fade: shown or not.
            if shown != self.visible {
                self.set_visible(shown)?;
            }
        } else {
            let key = (
                (opacity * 100.0).round() as i64,
                (breathing * 200.0).round() as i64,
            );
            if self.drawn != Some(key) {
                self.upload(opacity, breathing)?;
                self.drawn = Some(key);
            }
        }
        if shown {
            // Over everything, every draw: a window raised later (a menu, a
            // full-screen video) must not hide the frame for long. Not while
            // hidden: a locker's window stays on top.
            let connection = self.x11.connection();
            for strip in &self.strips {
                connection
                    .configure_window(
                        strip.window,
                        &ConfigureWindowAux::new().stack_mode(StackMode::ABOVE),
                    )
                    .map_err(|e| e.to_string())?;
            }
        }
        self.x11.connection().flush().map_err(|e| e.to_string())
    }

    fn upload(&self, opacity: f64, breathing: f64) -> Result<(), String> {
        let connection = self.x11.connection();
        let limit = connection
            .maximum_request_bytes()
            .saturating_sub(64)
            .max(4096);
        for strip in &self.strips {
            let (width, height) = (u32::from(strip.rect.2), u32::from(strip.rect.3));
            // The same premultiplied white as the Wayland buffers: on a
            // little-endian ARGB visual the byte order is B, G, R, A.
            let data = pixels(strip.edge, width, height, opacity, breathing);
            let row = width as usize * 4;
            let rows = (limit / row).max(1);
            for (band, chunk) in data.chunks(row * rows).enumerate() {
                let top = band * rows;
                connection
                    .put_image(
                        ImageFormat::Z_PIXMAP,
                        strip.window,
                        strip.gc,
                        strip.rect.2,
                        u16::try_from(chunk.len() / row).unwrap_or(u16::MAX),
                        0,
                        i16::try_from(top).unwrap_or(i16::MAX),
                        0,
                        32,
                        chunk,
                    )
                    .map_err(|e| e.to_string())?;
            }
        }
        Ok(())
    }

    pub fn poll(&mut self, timeout: Duration) -> Result<(), String> {
        let deadline = Instant::now() + timeout;
        loop {
            self.drain_events()?;
            if self.visible
                && self
                    .verified_at
                    .is_none_or(|at| at.elapsed() >= VERIFY_EVERY)
            {
                self.verify()?;
            }
            let now = Instant::now();
            if now >= deadline {
                return Ok(());
            }
            std::thread::sleep((deadline - now).min(Duration::from_millis(5)));
        }
    }

    /// Every strip was read back as viewable within the freshness window,
    /// and (with a compositor) carries a drawn gradient.
    #[must_use]
    pub fn presented(&self) -> bool {
        self.visible
            && (!self.composited || self.drawn.is_some_and(|(opacity, _)| opacity > 0))
            && self.presentation_age().is_some_and(|age| {
                age <= Duration::from_millis(crate::platform::linux::glow_state::MAX_AGE_MS)
            })
    }

    #[must_use]
    pub fn strip_count(&self) -> usize {
        self.strips.len()
    }

    #[must_use]
    pub fn presentation_age(&self) -> Option<Duration> {
        self.verified_at.map(|at| at.elapsed())
    }

    #[must_use]
    pub const fn presentation_serial(&self) -> u64 {
        self.serial
    }
}

impl Drop for X11Overlay {
    fn drop(&mut self) {
        let connection = self.x11.connection();
        for strip in &self.strips {
            let _ = connection.free_gc(strip.gc);
            let _ = connection.destroy_window(strip.window);
        }
        let _ = connection.flush();
    }
}
