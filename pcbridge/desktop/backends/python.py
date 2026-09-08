"""Adapters for the existing pure-Python desktop implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

from ...config import Config
from .. import capture as capturelib
from .. import input as inputlib
from .. import monitors as monitorslib
from .. import screencast as screencastlib
from .. import uitree as uitreelib


class PythonCaptureProvider:
    """Own the legacy ScreenCast handle and expose capture as one provider."""

    def __init__(self, cfg: Config, screencast: Any | None = None) -> None:
        self.cfg = cfg
        self.screencast = (
            screencast if screencast is not None else screencastlib.ScreenCast()
        )

    def start(self, *, cursor: bool | None = None) -> dict:
        monitors = [monitor.connector for monitor in self.list_monitors()]
        return self.screencast.start(
            monitors,
            cursor=self.cfg.desktop.include_pointer if cursor is None else cursor,
        )

    def close(self) -> None:
        self.screencast.close()

    def is_open(self) -> bool:
        return self.screencast.is_open()

    def available(self) -> tuple[bool, str]:
        return capturelib.available(self.screencast)

    def backend_name(self) -> str:
        return capturelib.backend_name(self.screencast)

    def capture(
        self,
        spec: int | str,
        *,
        out_dir: Path,
        scale_long_edge: int,
        include_pointer: bool,
    ) -> list[capturelib.Shot]:
        return capturelib.capture(
            spec,
            out_dir=out_dir,
            scale_long_edge=scale_long_edge,
            include_pointer=include_pointer,
            screencast=self.screencast,
        )

    def list_monitors(self) -> list[monitorslib.Monitor]:
        return monitorslib.list_monitors()

    def describe_monitors(self) -> str:
        return monitorslib.describe()

    def find_monitor(self, x: int, y: int) -> monitorslib.Monitor | None:
        return monitorslib.find_monitor(x, y)

    def to_global(
        self,
        x: int,
        y: int,
        *,
        monitor: int | str | None = None,
        shot: str | None = None,
        dirs: Sequence[Path] | None = None,
        guard_age: float = 0.0,
    ) -> tuple[int, int]:
        return capturelib.to_global(
            x,
            y,
            monitor=monitor,
            shot=shot,
            dirs=dirs,
            guard_age=guard_age,
        )

    def load_shot(
        self, shot_id: str, dirs: Sequence[Path]
    ) -> capturelib.Shot:
        return capturelib.load_shot(shot_id, dirs)

    def save_meta(
        self, shot: capturelib.Shot, out_dir: Path | None = None
    ) -> Path | None:
        return capturelib.save_meta(shot, out_dir)

    def oversize_note(self, shot: capturelib.Shot) -> str:
        return capturelib.oversize_note(shot)

    def kill_helpers(self) -> int:
        return screencastlib.kill_helpers()


class PythonInputProvider(inputlib.InputBackend):
    """Configure the existing lazy input backend from one Config object."""

    def __init__(self, cfg: Config) -> None:
        super().__init__(
            pointer_speed=cfg.desktop.pointer_speed,
            pointer_max_ms=cfg.desktop.pointer_move_max_ms,
            hold_max_seconds=cfg.desktop.hold_max_seconds,
            pos_file=cfg.pointer_pos_file,
        )


class PythonAccessibilityProvider(uitreelib.UiTree):
    """Expose the legacy AT-SPI client through the provider contract."""

    def available(self) -> tuple[bool, str]:
        return uitreelib.available()

    def describe_dump(self, dump: uitreelib.Dump) -> str:
        return uitreelib.describe(dump)

    def describe_windows(self, windows: list[uitreelib.Window]) -> str:
        return uitreelib.describe_windows(windows)


__all__ = [
    "PythonAccessibilityProvider",
    "PythonCaptureProvider",
    "PythonInputProvider",
]
