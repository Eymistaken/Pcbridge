"""Arka plan is (job) yoneticisi.

Her is ayri bir oturum grubunda (`start_new_session`) calisir. Durum bilgisi
diske yazilir, dolayisiyla servis restart'i sonrasi da sorgulanabilir.

ONEMLI -- ISLER SERVIS RESTART'INI ATLATMAZ. Bu dosya uzun sure "bu sayede
pcbridge yeniden baslasa bile isler devam eder" diye yaziyordu; OLCULDU
2026-08-03 ve YANLIS oldugu gorüldü. `start_new_session` yalnizca oturum/surec
grubunu ayiriyor, CGROUP'u degil: is pcbridge.service'in cgroup'unda kaliyor ve
systemd'nin varsayilan `KillMode=control-group` degeri `stop`/`restart`'ta onu
da olduruyor. Yani `systemctl --user restart pcbridge` calisan bir ajan isini
KESER.

Bunun iki sonucu var:
  1. Kod degistirip restart ederken uzun suren bir ajan isi varsa once
     `job_list` ile bakin.
  2. `systemctl --user stop pcbridge` gercek bir ACIL DURDURMA: gorsel ajanin
     elleri de durur (`computer_task` -> `bin/pcb-do`).

    <state_dir>/jobs/<job_id>/
        meta.json    -> is hakkinda bilgi
        out.log      -> birlesik stdout+stderr
        exit_code    -> is bitince yazilir
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])|\r")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def _short(value: Any, limit: int = 160) -> str:
    try:
        s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    except Exception:
        s = str(value)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def tail_chars(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return "…(kirpildi)…\n" + text[-limit:]


class JobStartError(RuntimeError):
    """A job could not be recorded, so it was not started (or was stopped)."""


class JobManager:
    def __init__(self, jobs_dir: Path, default_timeout: int = 1800):
        self.dir = jobs_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.default_timeout = default_timeout
        # Start every job in its own transient systemd scope
        # (pcbridge-job-<id>.scope). The resident daemon turns this on: a job
        # then survives the daemon being restarted or crashing, and an
        # explicit `pcbridge stop` still ends it (measured: a scoped job
        # outlived `kill -9` and `stop` of the service that started it; the
        # scope costs ~12 ms per job).
        self.use_scopes = False

    # ------------------------------------------------------------------ yollar
    def job_dir(self, job_id: str) -> Path:
        return self.dir / job_id

    def _meta_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "meta.json"

    def _log_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "out.log"

    def _exit_path(self, job_id: str) -> Path:
        return self.job_dir(job_id) / "exit_code"

    def read_meta(self, job_id: str) -> dict[str, Any]:
        p = self._meta_path(job_id)
        if not p.exists():
            raise KeyError(f"job not found: {job_id}")
        return json.loads(p.read_text(encoding="utf-8"))

    def _write_meta(self, job_id: str, meta: dict[str, Any]) -> None:
        self._meta_path(job_id).write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ----------------------------------------------------------------- baslat
    def _unwritable(self, exc: OSError) -> str:
        return (
            f"Cannot write the job record under {self.dir} ({exc.strerror or exc}); "
            "the job was NOT started. Free disk space or fix the permissions of "
            "pcbridge's state directory, then try again."
        )

    def start(
        self,
        *,
        kind: str,
        argv: list[str],
        cwd: Path,
        label: str = "",
        parser: str = "plain",
        timeout: int | None = None,
        extra: dict[str, Any] | None = None,
        env: dict[str, str] | None = None,
        pty: bool = False,
    ) -> str:
        job_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        jdir = self.job_dir(job_id)
        # Prove the record can be written BEFORE anything runs (Step 8 of
        # 2.0): on a full or read-only disk the process used to start and the
        # record write failed after it, leaving a job no tool could see or stop.
        try:
            jdir.mkdir(parents=True, exist_ok=True)
            probe = jdir / ".write-probe"
            probe.write_bytes(b"x" * 4096)
            probe.unlink()
        except OSError as exc:
            raise JobStartError(self._unwritable(exc)) from None

        log = self._log_path(job_id)
        exitf = self._exit_path(job_id)
        cmd_str = shlex.join(argv)

        if pty:
            # `script -qec` komutu sahte bir terminalde calistirir ve cikis
            # kodunu aynen dondurur. TTY yoksa ciktisini yutan CLI'lar icin.
            run_str = f"script -qec {shlex.quote(cmd_str)} /dev/null"
        else:
            run_str = cmd_str

        wrapper = (
            f"{run_str} > {shlex.quote(str(log))} 2>&1; "
            f"printf %s $? > {shlex.quote(str(exitf))}"
        )

        run_env = os.environ.copy()
        run_env.setdefault("TERM", "dumb")
        run_env["PCBRIDGE_JOB_ID"] = job_id
        if env:
            run_env.update(env)

        argv_run = ["bash", "-lc", wrapper]
        scope = None
        if self.use_scopes and shutil.which("systemd-run"):
            scope = f"pcbridge-job-{job_id}.scope"
            argv_run = [
                "systemd-run", "--user", "--scope", "--quiet", "--collect",
                f"--unit={scope}", "--", *argv_run,
            ]
        proc = subprocess.Popen(
            argv_run,
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=run_env,
        )

        to = int(timeout if timeout is not None else self.default_timeout)
        meta = {
            "id": job_id,
            "kind": kind,
            "label": label,
            "argv": argv,
            "command": cmd_str,
            "cwd": str(cwd),
            "parser": parser,
            "pid": proc.pid,
            "started_at": time.time(),
            "started_at_h": time.strftime("%Y-%m-%d %H:%M:%S"),
            "timeout": to,
            "pty": pty,
            "scope": scope,
            "deadline": time.time() + to if to > 0 else None,
            **(extra or {}),
        }
        try:
            self._write_meta(job_id, meta)
        except OSError as exc:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                pass
            raise JobStartError(self._unwritable(exc)) from None
        return job_id

    # ----------------------------------------------------------------- durum
    def _pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def status(self, job_id: str) -> dict[str, Any]:
        meta = self.read_meta(job_id)
        exitf = self._exit_path(job_id)

        if exitf.exists():
            raw = exitf.read_text(encoding="utf-8").strip() or "0"
            try:
                code = int(raw)
            except ValueError:
                code = -1
            meta["status"] = "finished" if code == 0 else "failed"
            meta["exit_code"] = code
            if "finished_at" not in meta:
                meta["finished_at"] = exitf.stat().st_mtime
                self._write_meta(job_id, meta)
        elif self._pid_alive(int(meta["pid"])):
            deadline = meta.get("deadline")
            if deadline and time.time() > deadline:
                self.cancel(job_id)
                meta["status"] = "timeout"
                meta["exit_code"] = None
            else:
                meta["status"] = "running"
                meta["exit_code"] = None
        else:
            meta["status"] = "vanished"
            meta["exit_code"] = None

        end = meta.get("finished_at") or time.time()
        meta["elapsed_seconds"] = round(end - meta["started_at"], 1)
        return meta

    def read_log(self, job_id: str) -> str:
        p = self._log_path(job_id)
        if not p.exists():
            return ""
        return p.read_text(encoding="utf-8", errors="replace")

    def list_jobs(self, limit: int = 20, only_running: bool = False) -> list[dict]:
        out: list[dict] = []
        for d in sorted(self.dir.iterdir(), reverse=True):
            if not d.is_dir() or not (d / "meta.json").exists():
                continue
            try:
                st = self.status(d.name)
            except Exception:
                continue
            if only_running and st["status"] != "running":
                continue
            out.append(
                {
                    "job_id": st["id"],
                    "kind": st["kind"],
                    "label": st.get("label", ""),
                    "status": st["status"],
                    "started": st["started_at_h"],
                    "elapsed_seconds": st["elapsed_seconds"],
                    "cwd": st["cwd"],
                }
            )
            if len(out) >= limit:
                break
        return out

    def cancel(self, job_id: str) -> str:
        meta = self.read_meta(job_id)
        pid = int(meta["pid"])
        if not self._pid_alive(pid):
            return "the job was not running"
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        for _ in range(12):
            time.sleep(0.25)
            if not self._pid_alive(pid):
                break
        if self._pid_alive(pid):
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        if not self._exit_path(job_id).exists():
            self._exit_path(job_id).write_text("130", encoding="utf-8")
        return "job stopped"

    def wait(self, job_id: str, seconds: float) -> dict[str, Any]:
        deadline = time.time() + seconds
        st = self.status(job_id)
        while st["status"] == "running" and time.time() < deadline:
            time.sleep(1.0)
            st = self.status(job_id)
        return st


# ---------------------------------------------------------------------------
# Cikti ayristiricilar
# ---------------------------------------------------------------------------


def parse_claude_stream_json(text: str) -> dict[str, Any]:
    """`claude -p --output-format stream-json --verbose` ciktisini ozetler."""
    session_id: str | None = None
    steps: list[str] = []
    result_text: str | None = None
    cost = duration_ms = num_turns = None
    is_error = False
    junk: list[str] = []
    init_model: str | None = None
    used_models: list[str] = []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if not line.startswith("{"):
            junk.append(strip_ansi(line))
            continue
        try:
            evt = json.loads(line)
        except json.JSONDecodeError:
            junk.append(strip_ansi(line))
            continue

        etype = evt.get("type")
        if etype == "system":
            if evt.get("subtype") == "init":
                session_id = evt.get("session_id") or session_id
                init_model = evt.get("model") or init_model
                steps.append(
                    f"· session started (model: {evt.get('model', '?')}, "
                    f"directory: {evt.get('cwd', '?')})"
                )
        elif etype == "assistant":
            for blk in (evt.get("message") or {}).get("content", []) or []:
                if blk.get("type") == "text" and blk.get("text", "").strip():
                    steps.append("· " + _short(blk["text"].strip(), 400))
                elif blk.get("type") == "tool_use":
                    steps.append(
                        f"→ tool: {blk.get('name')} ({_short(blk.get('input'), 120)})"
                    )
        elif etype == "user":
            for blk in (evt.get("message") or {}).get("content", []) or []:
                if blk.get("type") == "tool_result":
                    body = blk.get("content")
                    if isinstance(body, list):
                        body = " ".join(
                            b.get("text", "") for b in body if isinstance(b, dict)
                        )
                    steps.append("← result: " + _short(body, 120))
        elif etype == "result":
            session_id = evt.get("session_id") or session_id
            result_text = evt.get("result")
            cost = evt.get("total_cost_usd")
            duration_ms = evt.get("duration_ms")
            num_turns = evt.get("num_turns")
            is_error = bool(evt.get("is_error")) or evt.get("subtype") != "success"
            # `modelUsage` anahtarlari gercekten calisan model adlaridir. Istenen
            # modelle karsilastirilabilmesi icin disari verilir.
            usage = evt.get("modelUsage")
            if isinstance(usage, dict):
                used_models = [str(k) for k in usage if k]

    return {
        "session_id": session_id,
        "steps": steps,
        "final_answer": result_text,
        "is_error": is_error,
        "cost_usd": cost,
        "duration_ms": duration_ms,
        "num_turns": num_turns,
        "actual_model": ", ".join(used_models) if used_models else init_model,
        "warnings": [],
        "unparsed": junk[-20:],
    }


_ID_RE = re.compile(
    r"(?:conversation[_ -]?id|session[_ -]?id)\D{0,4}"
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)

# Model/effort secimi istendigi gibi gitmediginde CLI'in bastigi kaliplar.
# Bunlar goruldugunde is ozetinin EN USTUNE uyari basilir; yanlis modelle
# calismis bir is "basarili" gorunup gecip gitmesin.
_MODEL_WARN_PATTERNS: tuple[tuple[str, str], ...] = (
    (
        "using the default model instead",
        "The CLI ignored the requested model and fell back to its default.",
    ),
    (
        "requires --effort",
        "The model needed an effort as well; the call was refused.",
    ),
    (
        "invalid model selection",
        "Invalid model/effort combination — the CLI refused the call.",
    ),
)


def _scan_model_warnings(clean: str) -> list[str]:
    low = clean.lower()
    out: list[str] = []
    for needle, message in _MODEL_WARN_PATTERNS:
        if needle in low:
            out.append(message)
    return out


def _find_json_object(clean: str) -> dict[str, Any] | None:
    """Ciktidaki son ust duzey JSON nesnesini cozmeyi dene.

    agy hata verdiginde stdout bos kalir ve loga yalnizca `Error: ...` duser;
    o yuzden JSON bulunamamasi normal bir durum, hata degil.
    """
    for line in reversed(clean.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj
    return None


def parse_agy_json(text: str) -> dict[str, Any]:
    """`agy -p --output-format json` ciktisini ozetler.

    JSON alanlari (agy 1.1.9): conversation_id, status, response,
    duration_seconds, num_turns, usage{input,output,thinking,cache_read,total}.
    Model adi JSON'da YOK — gerek de yok: yanlis model/effort birlesimi sessizce
    calismiyor, exit 1 ile reddediliyor (bkz. PLAN.md (1.x, in git history) "Faz 0 sonuclari").
    """
    clean = strip_ansi(text)
    warnings = _scan_model_warnings(clean)
    obj = _find_json_object(clean)

    if obj is None:
        # JSON yok: ya is henuz bitmedi ya da CLI hata verip stderr'e yazdi.
        match = _ID_RE.search(clean)
        return {
            "session_id": match.group(1) if match else None,
            "steps": [],
            "final_answer": clean,
            "is_error": bool(warnings),
            "actual_model": None,
            "warnings": warnings,
        }

    status = str(obj.get("status", ""))
    usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
    return {
        "session_id": obj.get("conversation_id"),
        "steps": [],
        "final_answer": obj.get("response"),
        "is_error": bool(warnings) or (bool(status) and status.upper() != "SUCCESS"),
        "num_turns": obj.get("num_turns"),
        "duration_ms": (
            int(float(obj["duration_seconds"]) * 1000)
            if obj.get("duration_seconds") is not None
            else None
        ),
        "total_tokens": (usage or {}).get("total_tokens"),
        "actual_model": None,
        "warnings": warnings,
    }


def summarize(text: str, parser: str) -> dict[str, Any]:
    if parser == "claude_stream_json":
        return parse_claude_stream_json(text)
    if parser == "agy_json":
        return parse_agy_json(text)
    clean = strip_ansi(text)
    # Duz ciktida da bir konusma kimligi geciyorsa yakala; agy gibi CLI'larda
    # bu kimlikle `--conversation` uzerinden sohbete devam edilebiliyor.
    match = _ID_RE.search(clean)
    return {
        "session_id": match.group(1) if match else None,
        "steps": [],
        "final_answer": clean,
        "is_error": False,
        "actual_model": None,
        "warnings": _scan_model_warnings(clean),
    }
