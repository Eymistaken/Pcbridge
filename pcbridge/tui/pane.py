"""What every tab has in common: its footer keys and how the app reaches it."""

from __future__ import annotations

from typing import Any, ClassVar

from textual.containers import Vertical, VerticalScroll


def while_mounted(method):
    """Run a timer or worker callback only while its widgets exist.

    The 1 s timer and the workers' callbacks can land while the app is
    shutting down and its widgets are already gone (measured on a slow CI
    runner: NoMatches from `refresh_grant`); then there is nothing to update.
    """
    import functools

    from textual.css.query import NoMatches

    @functools.wraps(method)
    def run(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except NoMatches:
            return None

    return run


class PaneBase:
    """One tab's body.

    FOOTER lists this tab's keys as (key shown, label, action); an action
    is either an app action ("app.toggle_grant") or "key:X", which calls
    `key_X()` on the pane. The app binds every such key and hands it to the
    active pane, so a key works wherever the focus is, except inside an
    input that takes the character.
    """

    FOOTER: ClassVar[list[tuple[str, str, str]]] = []

    def shown(self) -> None:
        """The tab became active."""

    def status_changed(self, info: dict) -> None:
        """`pcbridge status` was read again (every 10 s)."""

    def config_changed(self) -> None:
        """The config was read again."""

    def dispatch_key(self, key: str) -> bool:
        method = getattr(self, f"key_{key}", None)
        if method is None:
            return False
        method()
        return True

    @property
    def pcb(self) -> Any:
        """The app, typed loosely: panes call its backend and helpers."""
        return self.app


class Pane(PaneBase, Vertical):
    """A tab whose parts size themselves (tables that fill the height)."""

    DEFAULT_CSS = """
    Pane {
        padding: 1 2 0 2;
    }
    """


class ScrollPane(PaneBase, VerticalScroll):
    """A tab that scrolls as a whole when the terminal is short."""

    DEFAULT_CSS = """
    ScrollPane {
        padding: 1 2 0 2;
        scrollbar-size-vertical: 1;
    }
    """
