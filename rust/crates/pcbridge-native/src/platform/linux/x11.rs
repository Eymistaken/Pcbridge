//! The shared X11 backend: Cinnamon and XFCE (stage 7 of the roadmap).
//!
//! One X connection answers what the Wayland desktops answer through their
//! compositors: the monitor table (RandR 1.5 monitors), a frame
//! (`GetImage` of the root window, the pointer from XFixes), input idle time
//! (the MIT-SCREEN-SAVER extension) and the windows (EWMH on the root
//! window). Input stays on uinput and accessibility on AT-SPI, as on Wayland.
//!
//! The connection is x11rb's pure Rust one: the helper links no libX11 or
//! libxcb. `DISPLAY` and `XAUTHORITY` come from the daemon, which takes them
//! from the user manager (measured on Debian 12 with LightDM: both are in
//! `systemctl --user show-environment`).
//!
//! X11 has no frame proof and no permission model for any of this: every
//! client of the display can read the screen and the windows. That is the
//! weaker security model `docs/security.md` describes; pcbridge's own gate
//! is the only protection.

use std::time::{Duration, Instant};

use pcbridge_core::display::{
    DisplayMode, DisplayState, LayoutMode, LogicalMonitor, PhysicalMonitor,
};
use pcbridge_core::frame::{FrameId, FrameSpec, PixelFormat, RgbaFrame};
use x11rb::connection::Connection;
use x11rb::protocol::randr::ConnectionExt as _;
use x11rb::protocol::screensaver::ConnectionExt as _;
use x11rb::protocol::xfixes::ConnectionExt as _;
use x11rb::protocol::xproto::{
    Atom, AtomEnum, ClientMessageEvent, ConnectionExt as _, EventMask, ImageFormat, ImageOrder,
    Window,
};
use x11rb::rust_connection::RustConnection;

/// The longest window title or class handed back, in characters.
pub const MAX_FIELD_CHARS: usize = 200;
/// The longest activation target accepted, in characters.
pub const MAX_TARGET_CHARS: usize = 200;
/// How long an activation waits for the window manager to confirm it.
const ACTIVATION_WAIT: Duration = Duration::from_millis(1000);
const ACTIVATION_POLL: Duration = Duration::from_millis(10);

#[derive(Debug, thiserror::Error)]
pub enum X11Error {
    #[error("no X display: {0}")]
    Connect(String),
    #[error("the X server refused a request: {0}")]
    Request(String),
    #[error("the X server lacks {0}")]
    Missing(&'static str),
    #[error("{0}")]
    Unsupported(String),
}

impl From<x11rb::errors::ConnectionError> for X11Error {
    fn from(error: x11rb::errors::ConnectionError) -> Self {
        Self::Request(error.to_string())
    }
}

impl From<x11rb::errors::ReplyError> for X11Error {
    fn from(error: x11rb::errors::ReplyError) -> Self {
        Self::Request(error.to_string())
    }
}

/// The atoms the backend reads, interned once per connection.
#[derive(Debug, Clone, Copy)]
struct Atoms {
    net_client_list: Atom,
    net_active_window: Atom,
    net_wm_name: Atom,
    net_wm_state: Atom,
    net_wm_state_skip_taskbar: Atom,
    net_wm_state_hidden: Atom,
    net_wm_window_type: Atom,
    net_wm_window_type_normal: Atom,
    net_wm_window_type_dialog: Atom,
    net_frame_extents: Atom,
    net_wm_pid: Atom,
    gtk_application_id: Atom,
    utf8_string: Atom,
    edid: Atom,
}

/// One top-level window as the window manager lists it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WindowInfo {
    pub id: Window,
    pub title: String,
    /// The application id GTK sets, when there is one (`org.gnome.Mousepad`).
    pub app_id: String,
    /// `WM_CLASS`: (instance, class).
    pub instance: String,
    pub class: String,
    pub skip_taskbar: bool,
    pub hidden: bool,
    pub normal: bool,
}

impl WindowInfo {
    /// The name pcbridge reports for the application: the GTK id, else the class.
    #[must_use]
    pub fn app(&self) -> String {
        let name = if self.app_id.is_empty() {
            &self.class
        } else {
            &self.app_id
        };
        truncate(name, MAX_FIELD_CHARS)
    }
}

/// A window's frame in root (canvas) coordinates: x, y, width, height.
pub type Frame = (i32, i32, u32, u32);

/// What `activate` did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Activation {
    pub activated: bool,
    pub ambiguous: bool,
    pub active: Option<WindowInfo>,
}

/// An open connection to the session's X display.
pub struct X11 {
    connection: RustConnection,
    root: Window,
    atoms: Atoms,
    started: Instant,
    sequence: std::sync::atomic::AtomicU64,
}

impl std::fmt::Debug for X11 {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("X11")
            .field("root", &self.root)
            .finish_non_exhaustive()
    }
}

impl X11 {
    /// Connect to `$DISPLAY` with `$XAUTHORITY`.
    pub fn connect() -> Result<Self, X11Error> {
        let (connection, screen) =
            x11rb::connect(None).map_err(|error| X11Error::Connect(error.to_string()))?;
        let root = connection
            .setup()
            .roots
            .get(screen)
            .ok_or_else(|| X11Error::Connect(format!("screen {screen} does not exist")))?
            .root;
        let atoms = intern_atoms(&connection)?;
        Ok(Self {
            connection,
            root,
            atoms,
            started: Instant::now(),
            sequence: std::sync::atomic::AtomicU64::new(0),
        })
    }

    #[must_use]
    pub const fn root(&self) -> Window {
        self.root
    }

    #[must_use]
    pub const fn connection(&self) -> &RustConnection {
        &self.connection
    }

    /// Which of the extensions this backend uses the server has.
    pub fn extensions(&self) -> Result<Vec<(&'static str, bool)>, X11Error> {
        let mut found = Vec::new();
        for name in ["RANDR", "MIT-SCREEN-SAVER", "XFIXES", "SHAPE"] {
            let present = self
                .connection
                .query_extension(name.as_bytes())?
                .reply()?
                .present;
            found.push((name, present));
        }
        Ok(found)
    }

    /// The monitor table as RandR 1.5 reports it.
    ///
    /// X11 positions and sizes are pixels and there is no per-monitor scale,
    /// so the state is Mutter's physical layout with scale 1. A rotated
    /// monitor's size is already its rotated size, so no transform is passed
    /// on: nothing here needs to swap axes again.
    pub fn display_state(&self) -> Result<DisplayState, X11Error> {
        let version = self.connection.randr_query_version(1, 5)?.reply()?;
        if (version.major_version, version.minor_version) < (1, 5) {
            return Err(X11Error::Unsupported(format!(
                "RandR {}.{} is too old; monitors need 1.5",
                version.major_version, version.minor_version
            )));
        }
        let reply = self
            .connection
            .randr_get_monitors(self.root, true)?
            .reply()?;
        let mut physical = Vec::new();
        let mut logical = Vec::new();
        for monitor in &reply.monitors {
            let name = self.atom_name(monitor.name)?;
            if name.is_empty() || monitor.width == 0 || monitor.height == 0 {
                return Err(X11Error::Unsupported(format!(
                    "RandR reported an unusable monitor '{name}'"
                )));
            }
            let edid = match monitor.outputs.first() {
                Some(output) => self.edid(*output).unwrap_or_default(),
                None => Vec::new(),
            };
            let identity = edid_identity(&edid);
            physical.push(PhysicalMonitor {
                connector: name.clone(),
                vendor: identity.vendor,
                product: identity.product,
                serial: identity.serial,
                display_name: if identity.name.is_empty() {
                    name.clone()
                } else {
                    identity.name
                },
                modes: vec![DisplayMode {
                    width: u32::from(monitor.width),
                    height: u32::from(monitor.height),
                    is_current: true,
                }],
            });
            logical.push(LogicalMonitor {
                x: i32::from(monitor.x),
                y: i32::from(monitor.y),
                scale: 1.0,
                transform: 0,
                primary: monitor.primary,
                connectors: vec![name],
            });
        }
        Ok(DisplayState {
            layout_mode: LayoutMode::Physical,
            physical,
            logical,
            primary_is_focus: false,
        })
    }

    fn edid(&self, output: u32) -> Result<Vec<u8>, X11Error> {
        let reply = self
            .connection
            .randr_get_output_property(
                output,
                self.atoms.edid,
                AtomEnum::ANY,
                0,
                128,
                false,
                false,
            )?
            .reply()?;
        Ok(reply.data)
    }

    fn atom_name(&self, atom: Atom) -> Result<String, X11Error> {
        let reply = self.connection.get_atom_name(atom)?.reply()?;
        Ok(String::from_utf8_lossy(&reply.name).into_owned())
    }

    /// One frame of the root window's `rect` (canvas coordinates), with the
    /// pointer drawn in when asked.
    pub fn capture(
        &self,
        rect: (i32, i32, u32, u32),
        include_pointer: bool,
    ) -> Result<(RgbaFrame, Instant), X11Error> {
        let (x, y, width, height) = rect;
        let (Ok(x16), Ok(y16), Ok(w16), Ok(h16)) = (
            i16::try_from(x),
            i16::try_from(y),
            u16::try_from(width),
            u16::try_from(height),
        ) else {
            return Err(X11Error::Unsupported(format!(
                "the region {width}x{height}+{x}+{y} is outside X11's coordinate range"
            )));
        };
        let setup = self.connection.setup();
        let reply = self
            .connection
            .get_image(ImageFormat::Z_PIXMAP, self.root, x16, y16, w16, h16, !0)?
            .reply()?;
        let received_at = Instant::now();
        let bits = setup
            .pixmap_formats
            .iter()
            .find(|format| format.depth == reply.depth)
            .map(|format| format.bits_per_pixel);
        if !(reply.depth == 24 || reply.depth == 32) || bits != Some(32) {
            return Err(X11Error::Unsupported(format!(
                "root window depth {} at {:?} bits per pixel; only 24/32-bit TrueColor is read",
                reply.depth, bits
            )));
        }
        let format = match setup.image_byte_order {
            ImageOrder::LSB_FIRST => PixelFormat::Bgrx,
            _ => {
                return Err(X11Error::Unsupported(
                    "the X server sends big-endian pixels".into(),
                ));
            }
        };
        let stride = width * 4;
        let spec = FrameSpec {
            format,
            width,
            height,
            stride,
            offset: 0,
            size: u32::try_from(reply.data.len()).unwrap_or(u32::MAX),
        };
        let id = FrameId {
            sequence: self
                .sequence
                .fetch_add(1, std::sync::atomic::Ordering::Relaxed)
                + 1,
            captured_at_ns: i64::try_from(received_at.duration_since(self.started).as_nanos())
                .unwrap_or(i64::MAX),
        };
        let mut frame = RgbaFrame::from_buffer(&spec, &reply.data, id)
            .map_err(|error| X11Error::Request(error.to_string()))?;
        if include_pointer {
            // A missing cursor image is not a failed capture: the pixels are
            // still the screen.
            if let Ok(cursor) = self.cursor() {
                draw_cursor(&mut frame, (x, y), &cursor);
            }
        }
        Ok((frame, received_at))
    }

    fn cursor(&self) -> Result<Cursor, X11Error> {
        self.connection.xfixes_query_version(4, 0)?.reply()?;
        let image = self.connection.xfixes_get_cursor_image()?.reply()?;
        Ok(Cursor {
            x: i32::from(image.x) - i32::from(image.xhot),
            y: i32::from(image.y) - i32::from(image.yhot),
            width: u32::from(image.width),
            height: u32::from(image.height),
            argb: image.cursor_image,
        })
    }

    /// Milliseconds since the last input of any device, uinput's included.
    pub fn idle_ms(&self) -> Result<u64, X11Error> {
        let present = self
            .connection
            .query_extension(b"MIT-SCREEN-SAVER")?
            .reply()?
            .present;
        if !present {
            return Err(X11Error::Missing("the MIT-SCREEN-SAVER extension"));
        }
        let info = self.connection.screensaver_query_info(self.root)?.reply()?;
        Ok(u64::from(info.ms_since_user_input))
    }

    /// The top-level windows the window manager manages, bottom to top.
    pub fn windows(&self) -> Result<Vec<WindowInfo>, X11Error> {
        let ids = self.window_list(self.atoms.net_client_list)?;
        Ok(ids
            .into_iter()
            .filter_map(|id| self.window_info(id).ok())
            .collect())
    }

    fn window_list(&self, property: Atom) -> Result<Vec<Window>, X11Error> {
        let reply = self
            .connection
            .get_property(false, self.root, property, AtomEnum::WINDOW, 0, 4096)?
            .reply()?;
        Ok(reply.value32().map(Iterator::collect).unwrap_or_default())
    }

    /// The window the window manager calls active, if any.
    pub fn active_window(&self) -> Result<Option<Window>, X11Error> {
        Ok(self
            .window_list(self.atoms.net_active_window)?
            .into_iter()
            .next()
            .filter(|id| *id != 0))
    }

    pub fn window_info(&self, id: Window) -> Result<WindowInfo, X11Error> {
        let title = self
            .text_property(id, self.atoms.net_wm_name, self.atoms.utf8_string)?
            .filter(|title| !title.is_empty())
            .map_or_else(
                || self.text_property(id, AtomEnum::WM_NAME.into(), AtomEnum::STRING.into()),
                |title| Ok(Some(title)),
            )?
            .unwrap_or_default();
        let app_id = self
            .text_property(id, self.atoms.gtk_application_id, self.atoms.utf8_string)?
            .unwrap_or_default();
        let class = self
            .connection
            .get_property(false, id, AtomEnum::WM_CLASS, AtomEnum::STRING, 0, 256)?
            .reply()?;
        let mut parts = class
            .value
            .split(|byte| *byte == 0)
            .map(|part| String::from_utf8_lossy(part).into_owned());
        let instance = parts.next().unwrap_or_default();
        let class = parts.next().unwrap_or_default();
        let state = self.atom_list(id, self.atoms.net_wm_state)?;
        let types = self.atom_list(id, self.atoms.net_wm_window_type)?;
        let normal = types.is_empty()
            || types.contains(&self.atoms.net_wm_window_type_normal)
            || types.contains(&self.atoms.net_wm_window_type_dialog);
        Ok(WindowInfo {
            id,
            title: truncate(&title, MAX_FIELD_CHARS),
            app_id: truncate(&app_id, MAX_FIELD_CHARS),
            instance: truncate(&instance, MAX_FIELD_CHARS),
            class: truncate(&class, MAX_FIELD_CHARS),
            skip_taskbar: state.contains(&self.atoms.net_wm_state_skip_taskbar),
            hidden: state.contains(&self.atoms.net_wm_state_hidden),
            normal,
        })
    }

    fn atom_list(&self, id: Window, property: Atom) -> Result<Vec<Atom>, X11Error> {
        let reply = self
            .connection
            .get_property(false, id, property, AtomEnum::ATOM, 0, 64)?
            .reply()?;
        Ok(reply.value32().map(Iterator::collect).unwrap_or_default())
    }

    fn text_property(
        &self,
        id: Window,
        property: Atom,
        kind: Atom,
    ) -> Result<Option<String>, X11Error> {
        let reply = self
            .connection
            .get_property(false, id, property, kind, 0, 1024)?
            .reply()?;
        if reply.type_ == x11rb::NONE || reply.format != 8 {
            return Ok(None);
        }
        Ok(Some(String::from_utf8_lossy(&reply.value).into_owned()))
    }

    /// `_NET_WM_PID` of `id`: the process the client says it is, or None.
    pub fn window_pid(&self, id: Window) -> Result<Option<u32>, X11Error> {
        let reply = self
            .connection
            .get_property(false, id, self.atoms.net_wm_pid, AtomEnum::CARDINAL, 0, 1)?
            .reply()?;
        Ok(reply.value32().and_then(|mut values| values.next()))
    }

    /// The window's frame (decorations included) in root coordinates.
    pub fn frame(&self, id: Window) -> Result<Frame, X11Error> {
        let geometry = self.connection.get_geometry(id)?.reply()?;
        let origin = self
            .connection
            .translate_coordinates(id, self.root, 0, 0)?
            .reply()?;
        let extents = self
            .connection
            .get_property(
                false,
                id,
                self.atoms.net_frame_extents,
                AtomEnum::CARDINAL,
                0,
                4,
            )?
            .reply()?;
        let [left, right, top, bottom] = match extents.value32() {
            Some(values) => {
                let values: Vec<u32> = values.collect();
                match values.as_slice() {
                    [left, right, top, bottom] => [*left, *right, *top, *bottom],
                    _ => [0; 4],
                }
            }
            None => [0; 4],
        };
        let x = i32::from(origin.dst_x) - i32::try_from(left).unwrap_or(0);
        let y = i32::from(origin.dst_y) - i32::try_from(top).unwrap_or(0);
        Ok((
            x,
            y,
            u32::from(geometry.width) + left + right,
            u32::from(geometry.height) + top + bottom,
        ))
    }

    /// Ask the window manager to activate the one window `target` names.
    ///
    /// The matching rules of the GNOME extension and the KWin script: the
    /// target is normalized and compared with the title, the class and the
    /// GTK application id; exact beats prefix or suffix beats substring; a tie
    /// is ambiguous and activates nothing; windows that skip the taskbar are
    /// not candidates. Activation is confirmed by reading
    /// `_NET_ACTIVE_WINDOW` back, never assumed.
    pub fn activate(&self, target: &str) -> Result<Activation, X11Error> {
        let query = normalize(target);
        let windows = if query.is_empty() || target.chars().count() > MAX_TARGET_CHARS {
            Vec::new()
        } else {
            self.windows()?
        };
        let ranked: Vec<(u8, &WindowInfo)> = windows
            .iter()
            .filter(|window| window.normal && !window.skip_taskbar)
            .filter_map(|window| {
                let score = [
                    &window.title,
                    &window.class,
                    &window.instance,
                    &window.app_id,
                ]
                .iter()
                .map(|value| field_score(&query, &normalize(value)))
                .max()
                .unwrap_or(0);
                (score > 0).then_some((score, window))
            })
            .collect();
        let best = ranked.iter().map(|(score, _)| *score).max().unwrap_or(0);
        let top: Vec<&WindowInfo> = ranked
            .iter()
            .filter(|(score, _)| *score == best)
            .map(|(_, window)| *window)
            .collect();
        let mut activated = false;
        if let [window] = top.as_slice() {
            self.request_activation(window.id)?;
            let deadline = Instant::now() + ACTIVATION_WAIT;
            loop {
                if self.active_window()? == Some(window.id) {
                    activated = true;
                    break;
                }
                if Instant::now() >= deadline {
                    break;
                }
                std::thread::sleep(ACTIVATION_POLL);
            }
        }
        let active = match self.active_window()? {
            Some(id) => self.window_info(id).ok(),
            None => None,
        };
        Ok(Activation {
            activated,
            ambiguous: top.len() > 1,
            active,
        })
    }

    /// Give the CLIPBOARD selection to no one. Any client may do this; the
    /// owner (an `xclip` pcbridge left behind, or an application) is told
    /// and lets go. Confirmed by reading the owner back.
    pub fn clear_clipboard(&self) -> Result<(), X11Error> {
        let clipboard = self
            .connection
            .intern_atom(false, b"CLIPBOARD")?
            .reply()?
            .atom;
        self.connection
            .set_selection_owner(x11rb::NONE, clipboard, x11rb::CURRENT_TIME)?;
        let owner = self
            .connection
            .get_selection_owner(clipboard)?
            .reply()?
            .owner;
        if owner == x11rb::NONE {
            Ok(())
        } else {
            Err(X11Error::Request(
                "another client kept the clipboard".into(),
            ))
        }
    }

    fn request_activation(&self, id: Window) -> Result<(), X11Error> {
        // Source indication 2: a pager, which window managers obey rather
        // than treating it as an application stealing the focus. The
        // timestamp 0 (CurrentTime) is what pagers send.
        let event = ClientMessageEvent::new(32, id, self.atoms.net_active_window, [2, 0, 0, 0, 0]);
        self.connection.send_event(
            false,
            self.root,
            EventMask::SUBSTRUCTURE_REDIRECT | EventMask::SUBSTRUCTURE_NOTIFY,
            event,
        )?;
        self.connection.flush()?;
        Ok(())
    }
}

fn intern_atoms(connection: &RustConnection) -> Result<Atoms, X11Error> {
    const NAMES: [&str; 14] = [
        "_NET_CLIENT_LIST",
        "_NET_ACTIVE_WINDOW",
        "_NET_WM_NAME",
        "_NET_WM_STATE",
        "_NET_WM_STATE_SKIP_TASKBAR",
        "_NET_WM_STATE_HIDDEN",
        "_NET_WM_WINDOW_TYPE",
        "_NET_WM_WINDOW_TYPE_NORMAL",
        "_NET_WM_WINDOW_TYPE_DIALOG",
        "_NET_FRAME_EXTENTS",
        "_NET_WM_PID",
        "_GTK_APPLICATION_ID",
        "UTF8_STRING",
        "EDID",
    ];
    let cookies = NAMES
        .iter()
        .map(|name| connection.intern_atom(false, name.as_bytes()))
        .collect::<Result<Vec<_>, _>>()?;
    let mut interned = std::collections::HashMap::new();
    for (name, cookie) in NAMES.iter().zip(cookies) {
        interned.insert(*name, cookie.reply()?.atom);
    }
    // By name, never by position: a name added in the middle of the list
    // once shifted every atom after it.
    let atom = |name: &str| interned[name];
    Ok(Atoms {
        net_client_list: atom("_NET_CLIENT_LIST"),
        net_active_window: atom("_NET_ACTIVE_WINDOW"),
        net_wm_name: atom("_NET_WM_NAME"),
        net_wm_state: atom("_NET_WM_STATE"),
        net_wm_state_skip_taskbar: atom("_NET_WM_STATE_SKIP_TASKBAR"),
        net_wm_state_hidden: atom("_NET_WM_STATE_HIDDEN"),
        net_wm_window_type: atom("_NET_WM_WINDOW_TYPE"),
        net_wm_window_type_normal: atom("_NET_WM_WINDOW_TYPE_NORMAL"),
        net_wm_window_type_dialog: atom("_NET_WM_WINDOW_TYPE_DIALOG"),
        net_frame_extents: atom("_NET_FRAME_EXTENTS"),
        net_wm_pid: atom("_NET_WM_PID"),
        gtk_application_id: atom("_GTK_APPLICATION_ID"),
        utf8_string: atom("UTF8_STRING"),
        edid: atom("EDID"),
    })
}

fn truncate(value: &str, limit: usize) -> String {
    value.chars().take(limit).collect()
}

/// The KWin script's and the extension's `normalize`, without NFKC: camel
/// case split, lower case, separators collapsed.
#[must_use]
pub fn normalize(value: &str) -> String {
    let mut split = String::with_capacity(value.len() + 8);
    let mut previous: Option<char> = None;
    for character in value.chars() {
        if let Some(before) = previous
            && (before.is_ascii_lowercase() || before.is_ascii_digit())
            && character.is_ascii_uppercase()
        {
            split.push(' ');
        }
        split.push(character);
        previous = Some(character);
    }
    split
        .to_lowercase()
        .split(|character: char| character.is_whitespace() || matches!(character, '.' | '_' | '-'))
        .filter(|part| !part.is_empty())
        .collect::<Vec<_>>()
        .join(" ")
}

/// 3: exact, 2: prefix or suffix, 1: substring, 0: no match.
#[must_use]
pub fn field_score(query: &str, value: &str) -> u8 {
    if value.is_empty() || query.is_empty() {
        0
    } else if value == query {
        3
    } else if value.starts_with(query) || value.ends_with(query) {
        2
    } else {
        u8::from(value.contains(query))
    }
}

#[derive(Debug, Default, PartialEq, Eq)]
pub struct EdidIdentity {
    pub vendor: String,
    pub product: String,
    pub serial: String,
    pub name: String,
}

/// Vendor, product code, serial and monitor name from an EDID block.
///
/// The serial is the descriptor string when there is one, else the 32-bit
/// number; a zero number is no serial. Anything malformed gives empty
/// fields, never a guess.
#[must_use]
pub fn edid_identity(edid: &[u8]) -> EdidIdentity {
    const HEADER: [u8; 8] = [0, 255, 255, 255, 255, 255, 255, 0];
    if edid.len() < 128 || edid[..8] != HEADER {
        return EdidIdentity::default();
    }
    let packed = u16::from_be_bytes([edid[8], edid[9]]);
    let letter =
        |shift: u16| char::from(b'@' + u8::try_from((packed >> shift) & 0x1f).unwrap_or(0));
    let vendor: String = [letter(10), letter(5), letter(0)].iter().collect();
    let product = format!("{:04X}", u16::from_le_bytes([edid[10], edid[11]]));
    let number = u32::from_le_bytes([edid[12], edid[13], edid[14], edid[15]]);
    let mut serial = if number == 0 {
        String::new()
    } else {
        number.to_string()
    };
    let mut name = String::new();
    for block in edid[54..126].chunks_exact(18) {
        if block[..3] != [0, 0, 0] {
            continue;
        }
        let text: String = block[5..]
            .iter()
            .take_while(|byte| **byte != b'\n')
            .map(|byte| char::from(*byte))
            .collect::<String>()
            .trim()
            .to_owned();
        match block[3] {
            0xFF if !text.is_empty() => serial = text,
            0xFC => name = text,
            _ => {}
        }
    }
    EdidIdentity {
        vendor,
        product,
        serial,
        name,
    }
}

#[derive(Debug)]
struct Cursor {
    x: i32,
    y: i32,
    width: u32,
    height: u32,
    /// Premultiplied ARGB, one `u32` per pixel.
    argb: Vec<u32>,
}

/// Blend the premultiplied cursor into the frame whose top-left is `origin`.
fn draw_cursor(frame: &mut RgbaFrame, origin: (i32, i32), cursor: &Cursor) {
    let frame_width = i64::from(frame.width);
    let frame_height = i64::from(frame.height);
    for row in 0..cursor.height {
        for column in 0..cursor.width {
            let Some(&pixel) = cursor
                .argb
                .get(usize::try_from(row * cursor.width + column).unwrap_or(usize::MAX))
            else {
                return;
            };
            let alpha = pixel >> 24;
            if alpha == 0 {
                continue;
            }
            let x = i64::from(cursor.x) + i64::from(column) - i64::from(origin.0);
            let y = i64::from(cursor.y) + i64::from(row) - i64::from(origin.1);
            if x < 0 || y < 0 || x >= frame_width || y >= frame_height {
                continue;
            }
            let Ok(index) = usize::try_from((y * frame_width + x) * 4) else {
                continue;
            };
            let source = [(pixel >> 16) & 0xff, (pixel >> 8) & 0xff, pixel & 0xff];
            for (channel, value) in source.iter().enumerate() {
                let below = u32::from(frame.pixels[index + channel]);
                let blended = value + below * (255 - alpha) / 255;
                frame.pixels[index + channel] = u8::try_from(blended.min(255)).unwrap_or(255);
            }
            frame.pixels[index + 3] = 255;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::{Cursor, draw_cursor, edid_identity, field_score, normalize};
    use pcbridge_core::frame::{FrameId, RgbaFrame};

    #[test]
    fn targets_normalize_like_the_extension() {
        assert_eq!(normalize("org.xfce.Mousepad"), "org xfce mousepad");
        assert_eq!(normalize("gnomeTextEditor"), "gnome text editor");
        assert_eq!(
            normalize("  Text_Editor -- notes.txt "),
            "text editor notes txt"
        );
        assert_eq!(normalize(""), "");
    }

    #[test]
    fn exact_beats_prefix_beats_substring() {
        assert_eq!(field_score("mousepad", "mousepad"), 3);
        assert_eq!(field_score("mousepad", "org xfce mousepad"), 2);
        assert_eq!(field_score("xfce", "org xfce mousepad"), 1);
        assert_eq!(field_score("kate", "mousepad"), 0);
        assert_eq!(field_score("", "mousepad"), 0);
    }

    fn edid_with(serial_number: u32, descriptors: &[(u8, &str)]) -> Vec<u8> {
        let mut edid = vec![0u8; 128];
        edid[..8].copy_from_slice(&[0, 255, 255, 255, 255, 255, 255, 0]);
        // "DEL": D=4, E=5, L=12.
        let packed: u16 = (4 << 10) | (5 << 5) | 12;
        edid[8..10].copy_from_slice(&packed.to_be_bytes());
        edid[10..12].copy_from_slice(&0xA0B1u16.to_le_bytes());
        edid[12..16].copy_from_slice(&serial_number.to_le_bytes());
        for (slot, (tag, text)) in descriptors.iter().enumerate() {
            let start = 54 + slot * 18;
            edid[start + 3] = *tag;
            let mut body = text.as_bytes().to_vec();
            body.push(b'\n');
            body.resize(13, b' ');
            edid[start + 5..start + 18].copy_from_slice(&body);
        }
        edid
    }

    #[test]
    fn edid_gives_vendor_product_serial_and_name() {
        let identity = edid_identity(&edid_with(1234, &[(0xFC, "DELL U2419H"), (0xFF, "ABC123")]));
        assert_eq!(identity.vendor, "DEL");
        assert_eq!(identity.product, "A0B1");
        assert_eq!(identity.serial, "ABC123");
        assert_eq!(identity.name, "DELL U2419H");
        let numeric = edid_identity(&edid_with(1234, &[]));
        assert_eq!(numeric.serial, "1234");
        assert_eq!(edid_identity(&edid_with(0, &[])).serial, "");
    }

    #[test]
    fn malformed_edid_gives_nothing() {
        assert_eq!(edid_identity(&[]), super::EdidIdentity::default());
        assert_eq!(edid_identity(&[0u8; 128]), super::EdidIdentity::default());
    }

    #[test]
    fn the_cursor_is_blended_at_its_position_and_clipped() {
        let mut frame = RgbaFrame {
            width: 2,
            height: 2,
            id: FrameId::default(),
            pixels: vec![10; 16],
        };
        let cursor = Cursor {
            x: 101,
            y: 50,
            width: 2,
            height: 1,
            // Opaque red, then half-transparent premultiplied white.
            argb: vec![0xFFFF_0000, 0x8080_8080],
        };
        draw_cursor(&mut frame, (100, 50), &cursor);
        // (0,0) untouched; (1,0) red; the half pixel at x=2 is outside.
        assert_eq!(&frame.pixels[0..4], &[10, 10, 10, 10]);
        assert_eq!(&frame.pixels[4..8], &[255, 0, 0, 255]);
        assert_eq!(&frame.pixels[8..16], &[10; 8]);
    }
}
