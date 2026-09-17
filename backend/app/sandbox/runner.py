"""Parent-side sandbox runner: policy check → isolated child process → watchdog."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.sandbox.policy import RUNTIME_IMPORT_ROOTS, validate_code

try:
    import psutil
except ImportError:  # pragma: no cover - psutil is a hard dependency, but degrade gracefully
    psutil = None

logger = logging.getLogger(__name__)

WORKER_PATH = Path(__file__).with_name("worker.py")
RESULT_MARKER = "<<<NUMERA_SANDBOX_RESULT>>>"
MAX_OUTPUT_BYTES = 60 * 1024 * 1024
POLL_INTERVAL_S = 0.05
PASSTHROUGH_ENV = ("SYSTEMROOT", "WINDIR", "PATH", "LANG", "LC_ALL", "TZ", "NUMBER_OF_PROCESSORS")


@dataclass
class ExecutionResult:
    ok: bool
    stdout: str = ""
    error: str | None = None
    error_type: str | None = None
    error_line: int | None = None
    kpis: list[dict[str, Any]] = field(default_factory=list)
    charts: list[dict[str, Any]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    policy_violations: list[str] = field(default_factory=list)
    duration_ms: int = 0
    timed_out: bool = False
    memory_exceeded: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SandboxRunner:
    def __init__(self, *, timeout_s: float = 60.0, memory_mb: int = 2048,
                 python_executable: str | None = None) -> None:
        self.timeout_s = timeout_s
        self.memory_bytes = memory_mb * 1024 * 1024
        self.python = python_executable or sys.executable

    def run(self, code: str, data_path: Path) -> ExecutionResult:
        started = time.perf_counter()
        violations = validate_code(code)
        if violations:
            return ExecutionResult(
                ok=False,
                error_type="PolicyViolation",
                error="Code rejected by the sandbox safety policy:\n- " + "\n- ".join(violations),
                policy_violations=violations,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )

        job = json.dumps({
            "code": code,
            "data_path": str(Path(data_path).resolve()),
            "runtime_import_roots": sorted(RUNTIME_IMPORT_ROOTS),
            "cpu_seconds": int(self.timeout_s) + 5,
        }).encode("utf-8")

        with tempfile.TemporaryDirectory(prefix="numera-sbx-", ignore_cleanup_errors=True) as workdir:
            result = self._spawn(job, workdir)
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        return result

    # ------------------------------------------------------------------------------
    def _spawn(self, job: bytes, workdir: str) -> ExecutionResult:
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        else:
            kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen(
                [self.python, "-I", str(WORKER_PATH)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=workdir, env=self._child_env(workdir), **kwargs,
            )
        except OSError as exc:
            return ExecutionResult(ok=False, error_type="SandboxError", error=f"Could not start sandbox: {exc}")

        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        overflow = threading.Event()
        readers = [
            threading.Thread(target=_drain, args=(proc.stdout, stdout_chunks, overflow), daemon=True),
            threading.Thread(target=_drain, args=(proc.stderr, stderr_chunks, overflow), daemon=True),
        ]
        for reader in readers:
            reader.start()

        try:
            assert proc.stdin is not None
            proc.stdin.write(job)
            proc.stdin.close()
        except OSError:
            pass  # the child died early; its stderr explains why

        timed_out = memory_exceeded = False
        deadline = time.monotonic() + self.timeout_s
        ps_proc = _ps_process(proc.pid)
        while proc.poll() is None:
            if time.monotonic() > deadline:
                timed_out = True
                break
            if overflow.is_set():
                break
            if ps_proc is not None and _rss(ps_proc) > self.memory_bytes:
                memory_exceeded = True
                break
            time.sleep(POLL_INTERVAL_S)

        if proc.poll() is None:
            _kill_tree(proc, ps_proc)
        proc.wait(timeout=10)
        for reader in readers:
            reader.join(timeout=5)

        if timed_out:
            return ExecutionResult(ok=False, timed_out=True, error_type="TimeoutError",
                                   error=f"Analysis exceeded the {self.timeout_s:.0f}s time limit. "
                                         "Simplify the computation or aggregate earlier.")
        if memory_exceeded:
            limit_mb = self.memory_bytes // (1024 * 1024)
            return ExecutionResult(ok=False, memory_exceeded=True, error_type="MemoryError",
                                   error=f"Analysis exceeded the {limit_mb} MB memory limit. "
                                         "Avoid large intermediate copies or cartesian joins.")
        if overflow.is_set():
            return ExecutionResult(ok=False, error_type="OutputTooLarge",
                                   error="Analysis produced too much output. Aggregate results before returning them.")

        raw = b"".join(stdout_chunks).decode("utf-8", errors="replace")
        marker_at = raw.rfind(RESULT_MARKER)
        if marker_at == -1:
            stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace").strip()
            logger.warning("Sandbox exited without a result (code %s): %s", proc.returncode, stderr[-2000:])
            return ExecutionResult(ok=False, error_type="SandboxCrash",
                                   error="The sandbox process exited unexpectedly"
                                         + (f": {stderr.splitlines()[-1]}" if stderr else "."))
        try:
            payload = json.loads(raw[marker_at + len(RESULT_MARKER):])
        except json.JSONDecodeError as exc:
            return ExecutionResult(ok=False, error_type="SandboxError", error=f"Malformed sandbox output: {exc}")

        known = {f for f in ExecutionResult.__dataclass_fields__}
        return ExecutionResult(**{k: v for k, v in payload.items() if k in known})

    @staticmethod
    def _child_env(workdir: str) -> dict[str, str]:
        # Deliberately minimal: API keys and other secrets never reach the sandbox.
        env = {k: os.environ[k] for k in PASSTHROUGH_ENV if k in os.environ}
        env.update({
            "PYTHONIOENCODING": "utf-8",
            "PYTHONHASHSEED": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "OMP_NUM_THREADS": "2",
            "OPENBLAS_NUM_THREADS": "2",
            "MKL_NUM_THREADS": "2",
            "TMP": workdir, "TEMP": workdir, "TMPDIR": workdir, "HOME": workdir,
        })
        return env


def _drain(stream, chunks: list[bytes], overflow: threading.Event) -> None:
    total = 0
    for chunk in iter(lambda: stream.read(65536), b""):
        total += len(chunk)
        if total > MAX_OUTPUT_BYTES:
            overflow.set()
            break
        chunks.append(chunk)
    stream.close()


def _ps_process(pid: int):
    if psutil is None:
        return None
    try:
        return psutil.Process(pid)
    except psutil.Error:
        return None


def _rss(ps_proc) -> int:
    try:
        total = ps_proc.memory_info().rss
        for child in ps_proc.children(recursive=True):
            total += child.memory_info().rss
        return total
    except psutil.Error:
        return 0


def _kill_tree(proc: subprocess.Popen, ps_proc) -> None:
    if ps_proc is not None:
        try:
            for child in ps_proc.children(recursive=True):
                child.kill()
        except psutil.Error:
            pass
    try:
        proc.kill()
    except OSError:
        pass
