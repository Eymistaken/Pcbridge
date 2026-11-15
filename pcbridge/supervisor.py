"""Keep the daemon running on Windows: what systemd does for it on Linux.

`pythonw -P -m pcbridge.supervisor` starts at logon (the per-user Run key,
see winservice) and runs `python -P -m pcbridge serve` with no window. The
daemon's stdout and stderr go to <state>/log/daemon.log, rotated at 5 MB.
The rules are those of systemd/pcbridge.service:

    exit 75   the daemon restarts itself to load a newer version: restart now
    exit 78   a configuration error: stay stopped until the file is fixed
    exit 0    asked to stop (`pcbridge stop`): the supervisor ends too
    other     a crash: restart after 1 s, doubling up to 30 s while it repeats

After every exit it runs `pcbridge.cli.lock`, as ExecStopPost does: the
desktop grant must not outlive the daemon. Only one supervisor runs per user
(a lock in the runtime directory).

The daemon gets a console of its own that nobody sees (CREATE_NO_WINDOW), so
the programs it starts inherit that hidden console instead of opening a
window each.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from . import oscompat
from . import paths as pathslib

RESTART_EXIT_CODE = 75
CONFIG_ERROR_EXIT_CODE = 78
LOCK_FILE = "supervisor.lock"
PID_FILE = "supervisor.pid"
LOG_FILE = "daemon.log"
LOG_ROTATE_BYTES = 5 * 1024 * 1024
LOG_KEEP = 3
CREATE_NO_WINDOW = 0x08000000
STABLE_SECONDS = 60.0


def log_path() -> Path:
    return pathslib.log_dir() / LOG_FILE


def console_python() -> str:
    """python.exe next to the running interpreter (which may be pythonw.exe)."""
    exe = Path(sys.executable)
    candidate = exe.with_name("python.exe")
    return str(candidate if candidate.exists() else exe)


def rotate(path: Path) -> None:
    try:
        if path.stat().st_size < LOG_ROTATE_BYTES:
            return
    except OSError:
        return
    for index in range(LOG_KEEP - 1, 0, -1):
        older = path.with_name(f"{path.name}.{index}")
        if older.exists():
            os.replace(older, path.with_name(f"{path.name}.{index + 1}"))
    os.replace(path, path.with_name(f"{path.name}.1"))


def _note(log, text: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    log.write(f"{stamp},000 INFO    pcbridge.supervisor: {text}\n".encode())
    log.flush()


def run(python: str | None = None) -> int:
    runtime = pathslib.runtime_dir(create=True)
    lock_fd = os.open(runtime / LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        oscompat.lock_fd(lock_fd, exclusive=True, blocking=False)
    except BlockingIOError:
        os.close(lock_fd)
        return 0  # another supervisor keeps the daemon
    (runtime / PID_FILE).write_text(str(os.getpid()), encoding="ascii")
    python = python or console_python()
    log_file = log_path()
    log_file.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONUTF8": "1"}
    backoff = 1.0
    try:
        while True:
            rotate(log_file)
            started = time.monotonic()
            with open(log_file, "ab") as log:
                _note(log, "starting the daemon")
                proc = subprocess.Popen(
                    [python, "-P", "-m", "pcbridge", "serve"],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    cwd=str(Path.home()), env=env, creationflags=CREATE_NO_WINDOW)
                code = proc.wait()
                _note(log, f"the daemon exited with code {code}")
                subprocess.run([python, "-P", "-m", "pcbridge.cli.lock"], stdin=subprocess.DEVNULL,
                               stdout=log, stderr=subprocess.STDOUT, env=env,
                               creationflags=CREATE_NO_WINDOW, check=False, timeout=60)
            if code == RESTART_EXIT_CODE:
                backoff = 1.0
                continue
            if code in (0, CONFIG_ERROR_EXIT_CODE):
                return code
            if time.monotonic() - started > STABLE_SECONDS:
                backoff = 1.0
            time.sleep(backoff)
            backoff = min(backoff * 2, 30.0)
    finally:
        try:
            (runtime / PID_FILE).unlink()
        except OSError:
            pass
        oscompat.unlock_fd(lock_fd)
        os.close(lock_fd)


def main() -> int:
    if not oscompat.IS_WINDOWS:
        print("pcbridge.supervisor is for Windows; systemd keeps the daemon on Linux.", file=sys.stderr)
        return 2
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
