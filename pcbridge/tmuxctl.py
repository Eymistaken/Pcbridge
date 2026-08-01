"""tmux ile canli terminal oturumlarini yonetme.

Bunun degeri: telefondan gonderdigin prompt, PC'de gercekten acik duran bir
terminale yazilir. Bilgisayarin basina gectiginde `tmux attach -t <isim>`
diyerek ayni oturuma girip kaldigin yerden devam edebilirsin.
"""

from __future__ import annotations

import shlex
import subprocess
import time

SOCKET_ARGS: list[str] = []  # gerekirse ["-L", "pcbridge"] yapilabilir


class TmuxError(RuntimeError):
    pass


def _run(args: list[str], timeout: int = 15) -> str:
    proc = subprocess.run(
        ["tmux", *SOCKET_ARGS, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout).strip()
        raise TmuxError(err or f"tmux {' '.join(args)} basarisiz")
    return proc.stdout


def available() -> bool:
    try:
        subprocess.run(["tmux", "-V"], capture_output=True, timeout=5, check=True)
        return True
    except Exception:
        return False


def exists(session: str) -> bool:
    try:
        _run(["has-session", "-t", f"={session}"])
        return True
    except TmuxError:
        return False


def list_sessions() -> list[dict]:
    try:
        out = _run(
            [
                "list-sessions",
                "-F",
                "#{session_name}\t#{session_windows}\t#{session_created}"
                "\t#{session_attached}\t#{pane_current_command}\t#{pane_current_path}",
            ]
        )
    except TmuxError as exc:
        msg = str(exc).lower()
        # Henuz hic oturum acilmadiysa tmux sunucusu da yoktur; bu bir hata degil.
        if any(
            s in msg
            for s in (
                "no server running",
                "error connecting",
                "failed to connect",
                "no such file or directory",
                "no sessions",
            )
        ):
            return []
        raise
    rows = []
    for line in out.strip().splitlines():
        parts = line.split("\t")
        while len(parts) < 6:
            parts.append("")
        rows.append(
            {
                "session": parts[0],
                "windows": parts[1],
                "created": time.strftime(
                    "%Y-%m-%d %H:%M", time.localtime(int(parts[2] or 0))
                ),
                "attached_on_pc": parts[3] == "1",
                "running": parts[4],
                "path": parts[5],
            }
        )
    return rows


def start(session: str, command: str | None, cwd: str) -> str:
    if exists(session):
        return f"'{session}' zaten acik"
    args = ["new-session", "-d", "-s", session, "-c", cwd, "-x", "200", "-y", "50"]
    if command:
        args.append(command)
    _run(args)
    try:
        _run(["set-option", "-t", session, "history-limit", "50000"])
    except TmuxError:
        pass
    return f"'{session}' olusturuldu"


def send_text(
    session: str, text: str, press_enter: bool = True, collapse_newlines: bool = True
) -> None:
    if not exists(session):
        raise TmuxError(f"'{session}' adinda acik bir oturum yok")
    payload = " ".join(text.split()) if collapse_newlines else text
    _run(["send-keys", "-t", session, "-l", payload])
    if press_enter:
        time.sleep(0.15)
        _run(["send-keys", "-t", session, "Enter"])


def send_keys(session: str, keys: list[str]) -> None:
    if not exists(session):
        raise TmuxError(f"'{session}' adinda acik bir oturum yok")
    _run(["send-keys", "-t", session, *keys])


def capture(session: str, lines: int = 60) -> str:
    if not exists(session):
        raise TmuxError(f"'{session}' adinda acik bir oturum yok")
    out = _run(["capture-pane", "-p", "-t", session, "-S", f"-{max(lines, 1)}"])
    stripped = [ln.rstrip() for ln in out.splitlines()]
    while stripped and not stripped[-1]:
        stripped.pop()
    return "\n".join(stripped[-lines:])


def kill(session: str) -> str:
    if not exists(session):
        return f"'{session}' zaten yok"
    _run(["kill-session", "-t", session])
    return f"'{session}' kapatildi"


def attach_hint(session: str) -> str:
    return f"tmux attach -t {shlex.quote(session)}"
