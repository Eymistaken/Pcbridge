//! One monitor's frame on KDE Plasma, from KWin's `ScreenShot2` interface.
//!
//! Measured on Plasma 6.7.5 (docs/dev/measured-facts.md): `CaptureScreen`
//! answers in 5-8 ms with no dialog, flash or sound, and writes the raw image
//! to a pipe we pass it. KWin allows it only for a program whose `.desktop`
//! file names this interface in `X-KDE-DBUS-Restricted-Interfaces` and names
//! the program as `Exec`; `pcbridge setup` installs that file for this
//! helper and nothing else, so the permission is the helper's alone, and the
//! helper refuses every capture that the desktop grant does not cover.
//!
//! There is no stream and no session to keep open: every frame is its own
//! call, which is also why a frame is always newer than the request.

use std::collections::HashMap;
use std::io::Read;
use std::os::fd::AsFd;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc;
use std::time::{Duration, Instant};

use pcbridge_core::frame::{FrameId, FrameSpec, PixelFormat, RgbaFrame};
use zbus::zvariant::{Fd, OwnedValue, Value};
use zbus::{Connection, Proxy, connection::Builder};

use crate::platform::linux::capture::{CaptureError, FrameIdentitySource, SourceFrame};

pub const SERVICE: &str = "org.kde.KWin.ScreenShot2";
const PATH: &str = "/org/kde/KWin/ScreenShot2";
pub const RESTRICTED_KEY: &str = "X-KDE-DBUS-Restricted-Interfaces";
const CALL_TIMEOUT: Duration = Duration::from_secs(5);

/// `QImage::Format` values KWin writes, to our packed layouts. Little endian:
/// a 32-bit ARGB pixel is B, G, R, A in memory.
///
/// The alpha byte is dropped: a monitor shows no transparency, and KWin's
/// alpha is blending residue (measured on Plasma 6.7.5: 2 of 1 024 000
/// pixels of an ARGB32_Premultiplied frame were below 255), which would
/// otherwise turn into holes in the PNG.
fn pixel_format(qimage_format: u32) -> Option<PixelFormat> {
    match qimage_format {
        // RGB32 (0xffRRGGBB), ARGB32, ARGB32_Premultiplied
        4..=6 => Some(PixelFormat::Bgrx),
        _ => None,
    }
}

fn result_u32(result: &HashMap<String, OwnedValue>, key: &str) -> Option<u32> {
    result
        .get(key)
        .and_then(|value| u32::try_from(value.clone()).ok())
}

/// Turn KWin's reply and the bytes it wrote into an owned frame.
pub fn frame_from_reply(
    result: &HashMap<String, OwnedValue>,
    data: &[u8],
    id: FrameId,
) -> Result<RgbaFrame, CaptureError> {
    let raw_format = result_u32(result, "format").unwrap_or(u32::MAX);
    let format = pixel_format(raw_format).ok_or(CaptureError::UnsupportedFormat(raw_format))?;
    let (Some(width), Some(height), Some(stride)) = (
        result_u32(result, "width"),
        result_u32(result, "height"),
        result_u32(result, "stride"),
    ) else {
        return Err(CaptureError::Stream(
            "KWin's reply has no width, height or stride".to_owned(),
        ));
    };
    let spec = FrameSpec {
        format,
        width,
        height,
        stride,
        offset: 0,
        size: u32::try_from(data.len()).unwrap_or(u32::MAX),
    };
    Ok(RgbaFrame::from_buffer(&spec, data, id)?)
}

/// A session-bus connection to KWin's screenshot service.
#[derive(Debug)]
pub struct KWinScreenShot {
    connection: Connection,
    started: Instant,
    sequence: AtomicU64,
}

impl KWinScreenShot {
    pub fn connect() -> Result<Self, CaptureError> {
        let connection = zbus::block_on(async {
            Builder::session()?
                .method_timeout(CALL_TIMEOUT)
                .build()
                .await
        })
        .map_err(|error| CaptureError::Unavailable(format!("session bus: {error}")))?;
        Ok(Self {
            connection,
            started: Instant::now(),
            sequence: AtomicU64::new(0),
        })
    }

    /// One frame of `connector`, at its native resolution.
    pub fn capture_screen(
        &self,
        connector: &str,
        include_pointer: bool,
    ) -> Result<SourceFrame, CaptureError> {
        let (mut reader, writer) =
            std::io::pipe().map_err(|error| CaptureError::Stream(format!("pipe: {error}")))?;
        // KWin writes from its own thread while the reply is on its way; read
        // concurrently so a full pipe never stalls it.
        let (sender, receiver) = mpsc::channel();
        std::thread::spawn(move || {
            let mut data = Vec::new();
            let result = reader.read_to_end(&mut data).map(|_| data);
            let _ = sender.send(result);
        });

        let mut options: HashMap<&str, Value<'_>> = HashMap::new();
        options.insert("include-cursor", Value::from(include_pointer));
        options.insert("native-resolution", Value::from(true));
        let reply: Result<HashMap<String, OwnedValue>, zbus::Error> = zbus::block_on(async {
            let proxy = Proxy::new(&self.connection, SERVICE, PATH, SERVICE).await?;
            proxy
                .call(
                    "CaptureScreen",
                    &(connector, options, Fd::from(writer.as_fd())),
                )
                .await
        });
        // Our copy of the write end goes now, or the reader never sees EOF.
        drop(writer);
        let received_at = Instant::now();
        let result = reply.map_err(|error| match error {
            zbus::Error::MethodError(name, message, _) if name.contains("NoAuthorized") => {
                CaptureError::Unavailable(format!(
                    "KWin refused the screenshot ({}): the helper is not authorized; \
                     run `pcbridge setup` to install its .desktop file",
                    message.unwrap_or_default()
                ))
            }
            other => CaptureError::Stream(format!("KWin ScreenShot2: {other}")),
        })?;
        let data = receiver
            .recv_timeout(CALL_TIMEOUT)
            .map_err(|_| CaptureError::Timeout(CALL_TIMEOUT))?
            .map_err(|error| CaptureError::Stream(format!("reading the frame: {error}")))?;

        let id = FrameId {
            sequence: self.sequence.fetch_add(1, Ordering::Relaxed) + 1,
            captured_at_ns: i64::try_from(received_at.duration_since(self.started).as_nanos())
                .unwrap_or(i64::MAX),
        };
        Ok(SourceFrame {
            frame: frame_from_reply(&result, &data, id)?,
            received_at,
            identity_source: FrameIdentitySource::SourceMonotonicClock,
        })
    }
}

/// The `.desktop` file that authorizes `executable`, if one is installed.
///
/// KWin reads the same directories (XDG data home, then the data dirs). Only
/// `pcbridge*.desktop` is looked at: the probe runs on every capabilities
/// request and must stay cheap.
#[must_use]
pub fn authorizing_desktop_file(executable: &std::path::Path) -> Option<PathBuf> {
    let home = std::env::var_os("XDG_DATA_HOME")
        .filter(|value| !value.is_empty())
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".local/share")));
    let dirs = std::env::var_os("XDG_DATA_DIRS")
        .filter(|value| !value.is_empty())
        .map_or_else(
            || {
                vec![
                    PathBuf::from("/usr/local/share"),
                    PathBuf::from("/usr/share"),
                ]
            },
            |value| std::env::split_paths(&value).collect(),
        );
    authorizing_desktop_file_in(home.into_iter().chain(dirs), executable)
}

/// `authorizing_desktop_file` over explicit data directories.
pub fn authorizing_desktop_file_in(
    data_dirs: impl IntoIterator<Item = PathBuf>,
    executable: &std::path::Path,
) -> Option<PathBuf> {
    let executable = executable.canonicalize().ok()?;
    data_dirs
        .into_iter()
        .map(|dir| dir.join("applications"))
        .filter_map(|dir| std::fs::read_dir(dir).ok())
        .flatten()
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| {
            path.file_name()
                .and_then(|name| name.to_str())
                .is_some_and(|name| name.starts_with("pcbridge") && name.ends_with(".desktop"))
        })
        .find(|path| desktop_file_authorizes(path, &executable))
}

fn desktop_file_authorizes(path: &std::path::Path, executable: &std::path::Path) -> bool {
    let Ok(text) = std::fs::read_to_string(path) else {
        return false;
    };
    let mut exec_matches = false;
    let mut interface_listed = false;
    for line in text.lines() {
        if let Some(value) = line.strip_prefix("Exec=") {
            exec_matches = value
                .split_whitespace()
                .next()
                .and_then(|program| std::path::Path::new(program).canonicalize().ok())
                .is_some_and(|program| program == executable);
        } else if let Some(value) = line.strip_prefix(RESTRICTED_KEY) {
            interface_listed = value
                .trim_start_matches('=')
                .split([',', ';'])
                .any(|name| name.trim() == SERVICE);
        }
    }
    exec_matches && interface_listed
}

#[cfg(test)]
mod tests {
    use super::{authorizing_desktop_file_in, frame_from_reply};
    use pcbridge_core::frame::FrameId;
    use std::collections::HashMap;
    use zbus::zvariant::{OwnedValue, Value};

    fn reply(format: u32, width: u32, height: u32, stride: u32) -> HashMap<String, OwnedValue> {
        let mut map = HashMap::new();
        for (key, value) in [
            ("format", format),
            ("width", width),
            ("height", height),
            ("stride", stride),
        ] {
            map.insert(
                key.to_owned(),
                OwnedValue::try_from(Value::from(value)).unwrap(),
            );
        }
        map
    }

    #[test]
    fn premultiplied_argb_becomes_opaque_rgba() {
        // One pixel, B G R A in memory with a stray alpha, and one padded row.
        let data = [0x30, 0x20, 0x10, 0x7f, 0, 0, 0, 0];
        let frame = frame_from_reply(&reply(6, 1, 1, 8), &data, FrameId::default()).unwrap();
        assert_eq!(frame.pixels, vec![0x10, 0x20, 0x30, 0xff]);
    }

    #[test]
    fn unknown_formats_are_refused() {
        let data = [0u8; 4];
        assert!(frame_from_reply(&reply(13, 1, 1, 4), &data, FrameId::default()).is_err());
    }

    #[test]
    fn only_a_file_naming_this_program_and_the_interface_authorizes() {
        let root = std::env::temp_dir().join(format!("pcbridge-kwin-{}", std::process::id()));
        let apps = root.join("applications");
        std::fs::create_dir_all(&apps).unwrap();
        let exe = std::env::current_exe().unwrap();
        let good = format!(
            "[Desktop Entry]\nExec={}\nX-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2\n",
            exe.display()
        );
        std::fs::write(apps.join("pcbridge-native.desktop"), &good).unwrap();
        std::fs::write(
            apps.join("pcbridge-other.desktop"),
            "Exec=/usr/bin/true\nX-KDE-DBUS-Restricted-Interfaces=org.kde.KWin.ScreenShot2\n",
        )
        .unwrap();
        let dirs = || vec![root.clone(), root.join("none")];
        assert_eq!(
            authorizing_desktop_file_in(dirs(), &exe),
            Some(apps.join("pcbridge-native.desktop"))
        );
        std::fs::write(
            apps.join("pcbridge-native.desktop"),
            good.replace("ScreenShot2", "X"),
        )
        .unwrap();
        assert_eq!(authorizing_desktop_file_in(dirs(), &exe), None);
        std::fs::remove_dir_all(&root).unwrap();
    }
}
