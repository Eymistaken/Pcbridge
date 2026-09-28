"""Read-only Hyprland readiness checks and setup guidance."""

from pathlib import Path
import json
import os
import stat

from .. import distro
from . import install as inst


def setup_notes() -> None:
    inst.ok("No GNOME extension, KWin permission entry, tray, or desktop bar is required")
    inst.say("Desktop control needs pcbridge-native with ext-image-copy-capture and output-source protocols, "
             "authoritative lock state, a fresh native idle watcher, and a proven visible grant frame. "
             "Unknown lock/idle state or missing frame proof refuses acting calls, even with force=true.")
    inst.say("Run pcbridge doctor to inspect this session. Hyprland detection alone does not establish support.")
    inst.say("For desktop screen sharing and file dialogs (independent of native image-copy capture): "
             + distro.install_command("xdg-desktop-portal", "xdg-desktop-portal-hyprland",
                                     "xdg-desktop-portal-gtk", "pipewire", "wireplumber"))


def _lease(directory: Path):
    """Inspect existing public grant state without creating a lease lockfile."""
    from ..desktop.lease import LeaseSnapshot, LEASE_STATE_FILE

    try:
        fd = os.open(directory / LEASE_STATE_FILE,
                     os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                return None
            raw = stream.read(65537)
        if len(raw) > 65536:
            return None
        data = json.loads(raw)
        return LeaseSnapshot.from_mapping(data) if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def doctor(doc, group: str) -> None:
    from ..desktop import session, safety, idlewatch, glowstate
    from ..native import discover_native_binary
    from ..native.diagnostics import handshake_capabilities

    instance = session.hyprland_instance()
    doc.add(group, "Hyprland IPC", "ok" if instance else "fail",
            "selected session IPC is available" if instance else "selected session IPC is unavailable or ambiguous",
            "" if instance else "run in the intended Hyprland session; check HYPRLAND_INSTANCE_SIGNATURE and WAYLAND_DISPLAY")
    platform = session.platform_summary()
    version = platform.get("hyprland")
    tested = version in session.TESTED_HYPRLAND_VERSIONS
    doc.add(group, "Hyprland version", "ok" if tested else "warn",
            f"{version} ({'tested' if tested else 'untested; detection is not support validation'})" if version
            else "version unavailable; detection is not support validation")
    binary = None
    if doc.cfg is not None:
        try:
            binary = discover_native_binary(doc.cfg.native)
        except Exception:  # Discovery errors are reported here without opening a grant.
            pass
    doc.add(group, "Hyprland native helper", "ok" if binary else "fail",
            str(binary) if binary else "native helper missing or unavailable",
            "" if binary else "install the pcbridge-native release helper; run pcbridge doctor")
    if binary:
        probed = handshake_capabilities(binary)
        caps = probed[1].get("capabilities", []) if isinstance(probed, tuple) else []
        capture = next((c for c in caps if isinstance(c, dict) and c.get("name") == "capture.monitor"), {})
        ready = (isinstance(probed, tuple) and probed[1].get("backend") == "linux.hyprland.image-copy"
                 and capture.get("status") == "supported")
        doc.add(group, "Hyprland capture protocol", "ok" if ready else "fail",
                "native image-copy/output-source probe passed; no capture started" if ready
                else str(probed) if isinstance(probed, str)
                else capture.get("reason", "native image-copy/output-source protocol unavailable"),
                "" if ready else "check the compositor capture protocols and native helper build")
    locked = safety.screen_locked()
    doc.add(group, "Hyprland lock state", "fail" if locked is None else "warn" if locked else "ok",
            "unknown: acting calls refuse" if locked is None else "locked: acting calls refuse" if locked else "authoritatively unlocked")
    idle = idlewatch.read_idle_ms()
    doc.add(group, "Hyprland idle time", "ok" if idle is not None else "fail",
            f"fresh native watcher: {idle} ms since input" if idle is not None
            else "unknown: acting calls refuse even with force=true",
            "" if idle is not None else "the resident daemon starts idle-watch when [desktop] enabled = true; check pcbridge logs")
    snapshot = _lease(Path(doc.cfg.state_dir)) if doc.cfg else None
    active = snapshot is not None and snapshot.is_active()
    token = snapshot.token() if active and snapshot.is_native_eligible() else None
    proof = (glowstate.read_on_current_outputs(Path(doc.cfg.state_dir), token, binary=binary)
             if token is not None and binary else None)
    doc.add(group, "Hyprland visible frame", "ok" if proof else "fail" if active else "info",
            "fresh trusted presentation proof covers current outputs for the active grant" if proof
            else "active grant has no trusted frame proof: acting calls refuse" if active
            else "required; no active grant, rendering has not been verified (doctor opens no grant)")
    # Static and socket-activated units are healthy when active. Their enablement
    # is not a requirement of pcbridge's native capture route.
    for unit, package in (("xdg-desktop-portal.service", "xdg-desktop-portal"),
                          ("xdg-desktop-portal-hyprland.service", "xdg-desktop-portal-hyprland"),
                          ("xdg-desktop-portal-gtk.service", "xdg-desktop-portal-gtk"),
                          ("pipewire.service", "pipewire"), ("wireplumber.service", "wireplumber")):
        active_state = inst.systemctl("is-active", unit).stdout.strip()
        doc.add("desktop integration", unit, "ok" if active_state == "active" else "warn",
                f"{active_state or 'unavailable'}; independent of native image-copy capture; active services do not prove portal routing",
                "" if active_state == "active" else distro.install_command(package))
