"""Keep the machine awake while jobs run (`[power] keep_awake`).

Off by default. When on, the daemon holds a sleep inhibitor while at least
one job is running and lets go when the last one ends.

Which inhibitor depends on the desktop, because each one honors a different
kind (measured in VMs on 2026-10-11, see docs/dev/measured-facts.md):

* GNOME: gsd-power checks gnome-session's inhibited actions before it
  suspends on idle, not logind's inhibitors (systemd 255 lets the same
  user's suspend through a logind block; 261 refuses it, so logind would
  work on Arch but not on Debian or Ubuntu). `gnome-session-inhibit
  --inhibit suspend` works on both. It does not touch the IDLE flag, so the
  screen still locks; with the lid closed GNOME then locks, not suspends.
* KDE Plasma: `kde-inhibit --power` asks PowerDevil itself. Idle suspend
  stops, the screen still locks, and Sleep from the menu still works.
  Without kde-inhibit, a logind `sleep` block is the fallback: PowerDevil
  honors it the same way, but on systemd 261 it also refuses a deliberate
  Sleep. A logind `idle` block would stop Plasma's screen lock too.
* Hyprland and everything else: hypridle and logind's own `IdleAction` honor
  logind `idle` inhibitors only, so `systemd-inhibit --what=idle` is used.
  hypridle skips EVERY timeout rule while it is held, the lock too, unless
  the rule sets `ignore_inhibit = true`. A deliberate suspend still works.

Every inhibitor is a child process that holds the lock while `cat` reads a
pipe from the daemon. The daemon never closes that pipe by accident: when
the daemon exits for any reason, even SIGKILL, the kernel closes it, `cat`
sees end of file and the inhibitor goes with it. It cannot outlive the
daemon.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger("pcbridge.power")

WHO = "pcbridge"
WHY = "pcbridge jobs are running"
# How long a fresh inhibitor must stay up before it counts as held: a refusal
# (no polkit, no gnome-session on the bus) ends the process at once.
SETTLE_S = 0.5
# After a failure, wait this long before the next attempt.
RETRY_S = 60.0
# PowerDevil applies an inhibition 5 s after it is asked; check it after that.
CONFIRM_AFTER_S = 6.0


@dataclass(frozen=True)
class Method:
    name: str            # "logind", "gnome-session" or "powerdevil"
    what: str            # what is inhibited, as shown to a person
    argv: tuple[str, ...]
    # The exit code does not say whether it took: kde-inhibit runs its command
    # and exits 0 even when PowerDevil is not there. True: `confirm()` checks.
    unconfirmed: bool = False


def _logind(what: str) -> Method:
    return Method("logind", what, (
        "systemd-inhibit", f"--what={what}", "--mode=block",
        f"--who={WHO}", f"--why={WHY}", "cat",
    ))


GNOME_SESSION = Method("gnome-session", "suspend", (
    "gnome-session-inhibit", "--app-id", WHO, "--reason", WHY,
    "--inhibit", "suspend", "cat",
))


POWERDEVIL = Method("powerdevil", "suspend", ("kde-inhibit", "--power", "cat"), unconfirmed=True)


def confirm(method: Method) -> bool:
    """Whether the desktop really took this inhibitor (for `unconfirmed` ones).

    kde-inhibit registers as app "Running Script" with its command as the
    reason, so PowerDevil lists ["Running Script", "cat"] while it holds.
    """
    if method.name != "powerdevil":
        return True
    try:
        out = subprocess.run(
            ["busctl", "--user", "--json=short", "call", "org.kde.Solid.PowerManagement",
             "/org/kde/Solid/PowerManagement/PolicyAgent",
             "org.kde.Solid.PowerManagement.PolicyAgent", "ListInhibitions"],
            capture_output=True, text=True, timeout=5,
        )
        data = json.loads(out.stdout or "{}").get("data") or [[]]
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return any(list(entry)[-1:] == ["cat"] for entry in data[0])


def methods_for(desktop: str) -> tuple[Method, ...]:
    """The inhibitors this desktop honors, best first (see the module docstring)."""
    kind = (desktop or "").lower()
    if kind == "gnome":
        return (GNOME_SESSION,)
    if kind == "kde":
        return (POWERDEVIL, _logind("sleep"))
    return (_logind("idle"),)


def method_for(desktop: str, which: Callable[[str], str | None] = shutil.which,
               skip: frozenset[str] | set[str] = frozenset()) -> Method:
    """The first installed inhibitor for this desktop that has not failed to
    take (`skip`); the best one if none is installed."""
    candidates = [m for m in methods_for(desktop) if m.name not in skip] or list(methods_for(desktop))
    return next((m for m in candidates if which(m.argv[0])), candidates[0])


def _desktop_kind() -> str:
    try:
        from .desktop import session as sessionlib

        return sessionlib.desktop_kind()
    except Exception:  # noqa: BLE001 - no desktop is a valid answer here
        return "unknown"


class KeepAwake:
    """Holds at most one inhibitor; `update()` decides, from the daemon's loop."""

    def __init__(
        self,
        *,
        desktop: Callable[[], str] = _desktop_kind,
        which: Callable[[str], str | None] = shutil.which,
        popen: Callable[..., Any] = subprocess.Popen,
        clock: Callable[[], float] = time.monotonic,
        settle_s: float = SETTLE_S,
        confirm: Callable[[Method], bool] = confirm,
    ) -> None:
        self._desktop = desktop
        self._which = which
        self._popen = popen
        self._clock = clock
        self._settle_s = settle_s
        self._confirm = confirm
        self._started = 0.0
        self._confirmed = False
        self._skip: set[str] = set()
        self._lock = threading.Lock()
        self._proc: Any = None
        self._method: Method | None = None
        self._since_wall: float | None = None
        self._enabled = False
        self._wanted = False
        self._reason = ""
        self._error = ""
        self._retry_at = 0.0

    # -- decisions --------------------------------------------------------------

    def update(self, enabled: bool, jobs_running: int) -> None:
        """Hold the inhibitor when enabled and a job runs; release it otherwise."""
        with self._lock:
            self._enabled = enabled
            self._wanted = enabled and jobs_running > 0
            self._reason = f"{jobs_running} job(s) running" if self._wanted else ""
            if not enabled:
                self._error = ""
            if not self._wanted:
                self._release()
                self._retry_at = 0.0
                self._skip.clear()       # the next job tries the best method again
                return
            if self._alive():
                if self._took():
                    return
                self._retry_at = 0.0     # try the next method now, not in a minute
            if self._proc is not None:
                self._fail(self._exit_reason())
            if self._clock() < self._retry_at:
                return
            self._acquire()

    def close(self) -> None:
        with self._lock:
            self._wanted = False
            self._release()

    # -- state -------------------------------------------------------------------

    def held(self) -> bool:
        with self._lock:
            return self._alive()

    def status(self) -> dict[str, Any]:
        """For status.json and `pcbridge status`. Read-only facts."""
        with self._lock:
            held = self._alive()
            return {
                "enabled": self._enabled,
                "held": held,
                "reason": self._reason if held else "",
                "method": self._method.name if held and self._method else "",
                "what": self._method.what if held and self._method else "",
                "since": int(self._since_wall) if held and self._since_wall else None,
                "error": "" if held else self._error,
            }

    # -- the child process ----------------------------------------------------------

    def _took(self) -> bool:
        """False once an unconfirmed inhibitor turns out not to have taken."""
        method = self._method
        if method is None or not method.unconfirmed or self._confirmed:
            return True
        if self._clock() - self._started < CONFIRM_AFTER_S:
            return True                  # too early to tell; held meanwhile
        if self._confirm(method):
            self._confirmed = True
            return True
        self._skip.add(method.name)
        self._fail(f"{method.argv[0]} ran but the desktop did not take the inhibition; "
                   f"trying the next method")
        return False

    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _acquire(self) -> None:
        method = method_for(self._desktop(), self._which, self._skip)
        if not self._which(method.argv[0]):
            self._fail(f"`{method.argv[0]}` is not installed; nothing can keep the "
                       f"machine awake on this desktop")
            return
        try:
            proc = self._popen(
                list(method.argv), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, close_fds=True,
            )
        except OSError as exc:
            self._fail(f"{method.argv[0]} did not start: {exc}")
            return
        self._proc, self._method = proc, method
        try:
            proc.wait(timeout=self._settle_s)
        except subprocess.TimeoutExpired:
            self._started = self._clock()
            self._confirmed = False
            self._since_wall = time.time()
            self._error = ""
            log.info("keep-awake: holding a %s %s inhibitor (%s)",
                     method.name, method.what, self._reason)
            return
        self._fail(self._exit_reason())

    def _exit_reason(self) -> str:
        proc, method = self._proc, self._method
        detail = ""
        try:
            if proc is not None and proc.stderr is not None:
                detail = proc.stderr.read().decode("utf-8", "replace").strip()
        except (OSError, ValueError):
            pass
        name = method.argv[0] if method else "the inhibitor"
        code = proc.returncode if proc is not None else None
        if detail:
            text = detail.splitlines()[-1]
        elif code is not None and code < 0:
            return f"{name} was stopped by signal {-code}; taking it again"
        else:
            text = f"exited with code {code}"
        hint = ""
        if "Access denied" in detail or "not authorized" in detail.lower():
            hint = " (logind needs polkit to grant inhibitors to a user)"
        return f"{name} refused: {text}{hint}"

    def _fail(self, message: str) -> None:
        if message != self._error:
            log.warning("keep-awake: %s", message)
        self._error = message
        self._retry_at = self._clock() + RETRY_S
        self._drop()

    def _release(self) -> None:
        if self._proc is None:
            return
        if self._alive():
            log.info("keep-awake: released the %s inhibitor", self._method.name if self._method else "")
        self._drop()

    def _drop(self) -> None:
        proc = self._proc
        self._proc = None
        self._method = None
        self._since_wall = None
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()   # `cat` sees end of file and exits
        except OSError:
            pass
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        for stream in (proc.stderr,):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass


def probe(desktop: str | None = None, *, timeout: float = 5.0) -> tuple[bool, str]:
    """Take the inhibitor for this desktop and give it back at once.

    For `pcbridge doctor`: (True, what) when it would work, (False, why) when
    not. Holding it for a moment changes nothing a person would notice.
    """
    kind = desktop if desktop is not None else _desktop_kind()
    method = method_for(kind)
    if not shutil.which(method.argv[0]):
        return False, f"`{method.argv[0]}` is not installed"
    if method.unconfirmed:
        # kde-inhibit exits 0 either way; what matters is PowerDevil on the bus.
        try:
            bus = subprocess.run(["busctl", "--user", "status", "org.kde.Solid.PowerManagement"],
                                 capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"PowerDevil could not be asked: {exc}"
        if bus.returncode == 0:
            return True, f"{method.name} {method.what} inhibitor"
        fallback = method_for(kind, skip={method.name})
        if fallback is method:
            return False, "PowerDevil is not running on the session bus"
        method = fallback
    argv = [*method.argv[:-1], "true"]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{method.argv[0]} failed: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        text = detail[-1] if detail else f"exited with code {proc.returncode}"
        hint = " (logind needs polkit to grant inhibitors to a user)" if "Access denied" in text else ""
        return False, f"{method.argv[0]} refused: {text}{hint}"
    return True, f"{method.name} {method.what} inhibitor"


__all__ = ["GNOME_SESSION", "POWERDEVIL", "KeepAwake", "Method", "method_for", "methods_for", "probe"]
