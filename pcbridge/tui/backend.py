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

    def tools(self, cfg: Any) -> list:
        from ..toolcatalog import build

        return build(cfg)
