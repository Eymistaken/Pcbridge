//! Which desktop the helper runs under.
//!
//! The same rule as `pcbridge.desktop.session.desktop_kind`: the
//! `XDG_CURRENT_DESKTOP` the parent passes through decides when it is set;
//! otherwise the compositor's name on the session bus does. Unknown sessions
//! remain unknown; a helper must never silently select Mutter for them.

use std::os::unix::fs::{FileTypeExt, MetadataExt};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};
use zbus::Connection;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DesktopKind {
    Gnome,
    Kde,
    Hyprland,
    Unknown,
}

impl DesktopKind {
    #[must_use]
    pub fn from_current_desktop(value: &str) -> Option<Self> {
        let tokens: Vec<&str> = value
            .split(':')
            .map(str::trim)
            .filter(|t| !t.is_empty())
            .collect();
        if tokens.is_empty() {
            return None;
        }
        if tokens
            .iter()
            .any(|t| t.to_ascii_lowercase().contains("gnome"))
        {
            return Some(Self::Gnome);
        }
        if tokens.iter().any(|t| t.eq_ignore_ascii_case("kde")) {
            return Some(Self::Kde);
        }
        if tokens.iter().any(|t| t.eq_ignore_ascii_case("hyprland")) {
            return Some(Self::Hyprland);
        }
        Some(Self::Unknown)
    }

    /// The desktop of this session (environment first, then the bus).
    #[must_use]
    pub fn detect() -> Self {
        if std::env::var_os("HYPRLAND_INSTANCE_SIGNATURE").is_some() {
            return Self::Hyprland;
        }
        if let Some(kind) = std::env::var("XDG_CURRENT_DESKTOP")
            .ok()
            .as_deref()
            .and_then(Self::from_current_desktop)
        {
            return kind;
        }
        if let Some(kind) = std::env::var("XDG_SESSION_DESKTOP")
            .ok()
            .as_deref()
            .and_then(Self::from_current_desktop)
        {
            return kind;
        }
        let owned = |name: &str| -> bool {
            zbus::block_on(async {
                let connection = Connection::session().await.ok()?;
                let proxy = zbus::fdo::DBusProxy::new(&connection).await.ok()?;
                let bus_name = zbus::names::BusName::try_from(name).ok()?;
                proxy.name_has_owner(bus_name).await.ok()
            })
            .unwrap_or(false)
        };
        if owned("org.gnome.Shell") {
            Self::Gnome
        } else if owned("org.kde.KWin") {
            Self::Kde
        } else {
            Self::Unknown
        }
    }
}

/// The same-user socket selected by the parent session environment.
/// Missing or contradictory session coordinates do not fall back to another
/// running compositor instance.
pub fn hyprland_socket() -> Option<PathBuf> {
    let signature = std::env::var("HYPRLAND_INSTANCE_SIGNATURE").ok()?;
    if signature.is_empty()
        || signature == "."
        || signature == ".."
        || !signature
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"_.-".contains(&byte))
    {
        return None;
    }
    let display = std::env::var("WAYLAND_DISPLAY").ok()?;
    if !display.strip_prefix("wayland-").is_some_and(|suffix| {
        !suffix.is_empty() && suffix.bytes().all(|byte| byte.is_ascii_digit())
    }) {
        return None;
    }
    if !listed_instance_pair(&signature, &display) {
        return None;
    }
    let uid = std::fs::metadata("/proc/self").ok()?.uid();
    let runtime = std::env::var_os("XDG_RUNTIME_DIR")
        .map_or_else(|| PathBuf::from(format!("/run/user/{uid}")), PathBuf::from);
    if runtime.metadata().ok()?.uid() != uid {
        return None;
    }
    let socket = runtime.join("hypr").join(signature).join(".socket.sock");
    for path in [&socket, &runtime.join(display)] {
        let info = path.metadata().ok()?;
        if info.uid() != uid || !info.file_type().is_socket() {
            return None;
        }
    }
    Some(socket)
}

fn listed_instance_pair(signature: &str, display: &str) -> bool {
    let Ok(mut child) = Command::new("hyprctl")
        .args(["-j", "instances"])
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
    else {
        return false;
    };
    let deadline = Instant::now() + Duration::from_secs(2);
    loop {
        match child.try_wait() {
            Ok(Some(status)) if status.success() => break,
            Ok(Some(_)) => return false,
            Err(_) => {
                let _ = child.kill();
                let _ = child.wait();
                return false;
            }
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(10)),
            Ok(None) => {
                let _ = child.kill();
                let _ = child.wait();
                return false;
            }
        }
    }
    let Ok(output) = child.wait_with_output() else {
        return false;
    };
    let Ok(data) = serde_json::from_slice::<serde_json::Value>(&output.stdout) else {
        return false;
    };
    instance_pair_in(&data, signature, display)
}

fn instance_pair_in(data: &serde_json::Value, signature: &str, display: &str) -> bool {
    data.as_array().is_some_and(|instances| {
        instances
            .iter()
            .filter(|item| item["instance"] == signature && item["wl_socket"] == display)
            .count()
            == 1
    })
}

#[cfg(test)]
mod tests {
    use super::{DesktopKind, instance_pair_in};

    #[test]
    fn the_environment_names_the_desktop() {
        assert_eq!(
            DesktopKind::from_current_desktop("zorin:GNOME"),
            Some(DesktopKind::Gnome)
        );
        assert_eq!(
            DesktopKind::from_current_desktop("KDE"),
            Some(DesktopKind::Kde)
        );
        assert_eq!(DesktopKind::from_current_desktop(""), None);
        assert_eq!(
            DesktopKind::from_current_desktop("sway"),
            Some(DesktopKind::Unknown)
        );
        assert_eq!(
            DesktopKind::from_current_desktop("Hyprland"),
            Some(DesktopKind::Hyprland)
        );
    }

    #[test]
    fn the_native_helper_requires_the_signature_and_wayland_socket_as_a_pair() {
        let instances = serde_json::json!([
            {"instance":"first","wl_socket":"wayland-1"},
            {"instance":"second","wl_socket":"wayland-2"}
        ]);
        assert!(instance_pair_in(&instances, "second", "wayland-2"));
        assert!(!instance_pair_in(&instances, "first", "wayland-2"));
        assert!(!instance_pair_in(&instances, "absent", "wayland-1"));
    }
}
