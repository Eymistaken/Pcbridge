"""What the terminal UI does, in one replaceable object.

The UI calls only these methods, so the tests drive the real widgets with a
fake backend and never touch the machine's config, grant or daemon.
"""

from __future__ import annotations

from typing import Any


class Backend:
    def load_config(self) -> Any:
        from ..config import load_config

        return load_config()

    def grant_state(self, cfg: Any) -> Any:
        from ..cli.grant import read_state

        return read_state(cfg)

    def lock(self, cfg: Any) -> str:
        from ..cli.grant import lock

        return lock(cfg)

    def unlock(self, cfg: Any, minutes: int | None) -> str:
        from ..cli.grant import unlock

        return unlock(cfg, minutes, "opened in the pcbridge terminal UI", granted_by="pcbridge ui")

    def status(self) -> dict:
        from ..cli.ops import status_data

        return status_data()

    def restart(self, cfg: Any) -> str:
        from ..cli.ops import _restart_when_idle

        return _restart_when_idle(cfg, 0).replace("`pcbridge update`", "`pcbridge restart`")

    def editor(self) -> Any:
        from ..settings import ConfigEditor

        return ConfigEditor()

    def panel_icon_status(self) -> tuple[str | None, str]:
        """The GNOME icon mode and any reason it cannot take effect yet."""
        from ..desktop import panelicon, session

        unsupported = session.support_note()
        if unsupported:
            return None, unsupported
        kind = session.desktop_kind()
        if kind == session.KDE:
            return None, "KDE Plasma has no pcbridge panel icon."
        if kind == session.HYPRLAND:
            return None, ("Hyprland has no pcbridge GNOME panel icon. No panel or tray is required; "
                          "the native glow signals granted desktop control.")
        if kind != session.GNOME:
            return None, "No GNOME session was detected."
        try:
            mode = panelicon.get_mode()
        except panelicon.PanelIconError as exc:
            return None, str(exc)
        version = panelicon.running_version()
        if version is None:
            note = "The extension is not running in this session; the setting applies when it runs."
        elif not panelicon.running_supports_mode(version):
            note = f"The running extension is version {version}; the setting applies after the next login."
        else:
            note = "Changes apply immediately and persist across logins."
        return mode, note

    def set_panel_icon_mode(self, mode: str) -> tuple[str | None, str]:
        from ..desktop import panelicon, session

        unsupported = session.support_note()
        if unsupported:
            raise panelicon.PanelIconError(unsupported)
        if session.desktop_kind() != session.GNOME:
            raise panelicon.PanelIconError("This session has no pcbridge GNOME panel icon.")
        panelicon.set_mode(mode)
        return self.panel_icon_status()

    def tools(self, cfg: Any) -> list:
        from ..toolcatalog import build

        return build(cfg)

    def clients(self) -> list[dict]:
        """Every MCP client pcbridge can connect, with its state."""
        from ..cli import connect as c
        from ..cli import install as inst

        cmd = inst.client_command()
        out = []
        for client in c.ALL_CLIENTS:
            st, reg = c.state(client, cmd)
            out.append({"client": client, "name": c.NAMES[client], "state": st,
                        "command": reg.command if reg.present else None, "config": reg.source,
                        "note": reg.note, "setup_default": client in c.CLIENTS})
        return out

    def set_connection(self, client: str, connect: bool) -> tuple[str, str]:
        """Connect or disconnect one client; (ok|warn|skip, what happened)."""
        from ..cli import connect as c
        from ..cli import install as inst

        backup = inst.Backup()
        results = c.connect([client], backup) if connect else c.disconnect([client], backup)
        _, status, detail = results[0]
        if backup.entries:
            backup.write_rollback()
            detail += f" (backup in {backup.root})"
        return status, detail

    def remote_clients(self, cfg: Any) -> dict:
        """The remote apps that hold tokens, and what the HTTP path offers."""
        import time

        from .. import remoteclients as rc

        if cfg is None:
            raise RuntimeError("the config does not load")
        groups = rc.clients(cfg.db_path)
        remote = getattr(cfg, "remote_tools_profile", "same")
        return {
            "clients": [{"name": g.name, "connected": len(g.connected),
                         "registrations": len(g.registrations), "expires": g.expires,
                         "last": time.strftime("%Y-%m-%d %H:%M", time.localtime(g.last_registered))}
                        for g in groups],
            "registrations": len(rc.registrations(cfg.db_path)),
            "static_token": bool(getattr(cfg, "static_token", "")),
            "has_password": bool(getattr(cfg, "password", "")),
            "remote_profile": cfg.tools_profile if remote == "same" else remote,
            "remote_profile_set": remote != "same",
        }

    def set_remote_password(self, cfg: Any, password: str) -> Any:
        """Store the hash of a new remote password; the SaveResult."""
        from .. import remotepassword

        return remotepassword.save(password, getattr(cfg, "source_path", None))

    def connected_remote(self, cfg: Any) -> int:
        from .. import remoteclients as rc

        return rc.connected_count(cfg.db_path) if cfg is not None else 0

    def revoke_remote(self, cfg: Any, name: str | None) -> str:
        """Revoke one app by name, or every one with None; what happened."""
        from .. import remoteclients as rc

        if cfg is None:
            raise RuntimeError("the config does not load")
        if name is None:
            res = rc.revoke_all(cfg.db_path, cfg.audit_log)
        else:
            res = rc.revoke(cfg.db_path, [r.client_id for r in rc.select(cfg.db_path, name)],
                            cfg.audit_log)
        return (f"Revoked {', '.join(res.names) or 'nothing'}: {res.registrations} registration(s), "
                f"{res.refresh_tokens} refresh token(s).")
