"""Run one background job on Windows: what the bash wrapper does on Linux.

    python -P -m pcbridge.jobrunner LOG EXIT [--pty] -- PROGRAM ARG...

On Linux a job is `bash -lc 'CMD > LOG 2>&1; printf %s $? > EXIT'`. Windows
has no bash, so this process runs the job's argv directly (no shell parses
it), sends its output to LOG, and writes the exit code to EXIT when it ends.
pcbridge records this process's pid, so `job_cancel` ends it and the job
with it (`taskkill /T`). It starts in its own process group with no window
and outlives a daemon restart, as a job's systemd scope does on Linux.

`--pty` runs the job in a ConPTY (pywinpty), for programs that print nothing
without a terminal, as `script -qec` does on Linux; without pywinpty the job
runs plainly and LOG says so.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000


def _write_exit(path: Path, code: int) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(str(code), encoding="utf-8")
    os.replace(temporary, path)


def _run_pty(argv: list[str], log) -> int:
    from winpty import PtyProcess  # pywinpty

    proc = PtyProcess.spawn(subprocess.list2cmdline(argv), dimensions=(50, 200))
    while True:
        try:
            data = proc.read(65536)
        except EOFError:
            break
        if data:
            log.write(data.encode("utf-8", "replace"))
            log.flush()
        elif not proc.isalive():
            break
    proc.wait()
    return int(proc.exitstatus or 0)


def run(log_path: Path, exit_path: Path, argv: list[str], pty: bool) -> int:
    code = -1
    try:
        with open(log_path, "ab", buffering=0) as log:
            if pty:
                try:
                    code = _run_pty(argv, log)
                    return code
                except ImportError:
                    log.write(b"[pcbridge] pywinpty is not installed; running without a terminal\n")
            try:
                proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                        creationflags=CREATE_NO_WINDOW)
            except OSError as exc:
                log.write(f"[pcbridge] cannot start {argv[0]}: {exc}\n".encode("utf-8", "replace"))
                code = 127
                return code
            code = proc.wait()
            return code
    finally:
        _write_exit(exit_path, code)


def main(args: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if args is None else args)
    if len(args) < 4 or "--" not in args:
        print("usage: python -m pcbridge.jobrunner LOG EXIT [--pty] -- PROGRAM ARG...", file=sys.stderr)
        return 2
    split = args.index("--")
    head, argv = args[:split], args[split + 1:]
    pty = "--pty" in head
    paths = [a for a in head if a != "--pty"]
    if len(paths) != 2 or not argv:
        print("usage: python -m pcbridge.jobrunner LOG EXIT [--pty] -- PROGRAM ARG...", file=sys.stderr)
        return 2
    return run(Path(paths[0]), Path(paths[1]), argv, pty)


if __name__ == "__main__":
    raise SystemExit(main())
