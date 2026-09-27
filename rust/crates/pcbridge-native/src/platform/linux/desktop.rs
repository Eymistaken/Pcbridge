//! Which desktop the helper runs under.
//!
//! The same rule as `pcbridge.desktop.session.desktop_kind`: the
//! `XDG_CURRENT_DESKTOP` the parent passes through decides when it is set;
//! otherwise the compositor's name on the session bus does. Unknown sessions
//! remain unknown; a helper must never silently select Mutter for them.

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

#[cfg(test)]
mod tests {
    use super::DesktopKind;

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
}
