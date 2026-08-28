# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

import json
import os
import subprocess
import threading
import uuid
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from marimo import _loggers
from marimo._r.interop import bytes_to_arrow_table
from marimo._r.launcher import resolve_r_invocation

LOGGER = _loggers.marimo_logger()


@dataclass
class RResponse:
    ok: bool
    stdout: list[str]
    stderr: list[str]
    value: dict[str, Any] | None
    plot_path: Optional[str]
    plot_error: Optional[str]
    error: Optional[str]


class RSession:
    def __init__(self) -> None:
        self._process: Optional[subprocess.Popen[str]] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        backend_path = (
            Path(__file__).resolve().parent / "resources" / "r_backend.R"
        )
        invocation = resolve_r_invocation()
        command = [
            invocation.binary,
            # Skips ~/.Renviron and ~/.Rprofile. Note this does *not* isolate
            # the library path on its own; resolve_r_invocation sanitizes the
            # R_LIBS* variables that --vanilla leaves alone.
            "--vanilla",
            "--quiet",
            "--slave",
            "-f",
            str(backend_path),
        ]
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=invocation.env,
        )

    def close(self) -> None:
        if self._process is None:
            return
        if self._process.poll() is None:
            self._process.terminate()
        if self._process.stdin is not None:
            self._process.stdin.close()
        if self._process.stdout is not None:
            self._process.stdout.close()
        if self._process.stderr is not None:
            self._process.stderr.close()
        self._process = None

    def reset(self) -> None:
        self.close()
        self.start()

    def execute(
        self,
        code: str,
        *,
        inputs: Optional[dict[str, Any]] = None,
        capture: bool = True,
        plot: bool = True,
        plot_format: str = "png",
        plot_width: int = 960,
        plot_height: int = 640,
        plot_dpi: int = 120,
    ) -> RResponse:
        self.start()
        if self._process is None or self._process.stdin is None:
            raise RuntimeError("R process is not available")
        if self._process.poll() is not None:
            stderr_output = ""
            if self._process.stderr is not None:
                stderr_output = self._process.stderr.read()
            message = "R process exited before handling the request"
            if stderr_output:
                message = f"{message}: {stderr_output.strip()}"
            raise RuntimeError(message)

        request = {
            "id": uuid.uuid4().hex,
            "code": code,
            "inputs": inputs or {},
            "capture": capture,
            "plot": plot,
            "plot_format": plot_format,
            "plot_width": plot_width,
            "plot_height": plot_height,
            "plot_dpi": plot_dpi,
        }

        payload = json.dumps(request)

        # Register the R process on the execution context so the
        # SIGINT handler can forward the interrupt to the R subprocess.
        from marimo._runtime.context.types import safe_get_context

        ctx = safe_get_context()
        exec_ctx = ctx.execution_context if ctx is not None else None
        r_ctx = (
            exec_ctx.with_r_process(self._process)
            if exec_ctx is not None
            else nullcontext()
        )

        try:
            with r_ctx:
                with self._lock:
                    self._process.stdin.write(payload + os.linesep)
                    self._process.stdin.flush()
                    if self._process.stdout is None:
                        raise RuntimeError("R process stdout is not available")
                    response_line = self._process.stdout.readline()
        except KeyboardInterrupt:
            # The interrupt handler sent SIGINT to the R subprocess.
            # Reset the session to avoid out-of-sync stdin/stdout state.
            LOGGER.debug("R execution interrupted, resetting session")
            self.reset()
            raise
        if not response_line:
            stderr_output = ""
            if self._process.stderr is not None:
                stderr_output = self._process.stderr.read()
            message = "R process did not return a response"
            if stderr_output:
                message = f"{message}: {stderr_output.strip()}"
            raise RuntimeError(message)

        response = json.loads(response_line)
        value = response.get("value")
        plot_info = response.get("plot") or {}
        if isinstance(plot_info, dict) and "value" in plot_info:
            plot_info.pop("value")

        # auto_unbox in toJSON turns length-1 character vectors
        # into bare strings instead of arrays; normalise to list.
        raw_stdout = response.get("stdout") or []
        raw_stderr = response.get("stderr") or []
        if isinstance(raw_stdout, str):
            raw_stdout = [raw_stdout]
        if isinstance(raw_stderr, str):
            raw_stderr = [raw_stderr]

        return RResponse(
            ok=bool(response.get("ok")),
            stdout=raw_stdout,
            stderr=raw_stderr,
            value=value,
            plot_path=plot_info.get("path"),
            plot_error=plot_info.get("error"),
            error=response.get("error"),
        )

    def to_arrow_table(self, value: dict[str, Any]) -> Any:
        kind = value.get("kind")
        if kind == "arrow_file":
            # File-based Arrow IPC: read and delete the temp file
            path = value.get("path", "")
            if not path or not os.path.isfile(path):
                raise ValueError(f"Arrow IPC temp file not found: {path}")
            import pyarrow as pa  # type: ignore

            try:
                table = pa.ipc.open_stream(pa.memory_map(path, "r")).read_all()
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
            return table
        if kind != "arrow":
            raise ValueError("Expected Arrow payload")
        data = value.get("data")
        if data is None:
            raise ValueError("Arrow payload missing data")
        if isinstance(data, str):
            # base64-encoded Arrow IPC bytes
            import base64

            payload = base64.b64decode(data)
        elif isinstance(data, list):
            # Legacy: list-of-ints format
            payload = bytes(data)
        else:
            payload = bytes(data)
        return bytes_to_arrow_table(payload)


_GLOBAL_SESSION: Optional[RSession] = None


def get_session() -> RSession:
    from marimo._runtime.context.types import safe_get_context

    context = safe_get_context()
    if context is None:
        global _GLOBAL_SESSION
        if _GLOBAL_SESSION is None:
            _GLOBAL_SESSION = RSession()
        return _GLOBAL_SESSION

    session = getattr(context, "_r_session", None)
    if session is None:
        session = RSession()
        context._r_session = session  # type: ignore[attr-defined]
    return session


def reset_session() -> None:
    from marimo._runtime.context.types import safe_get_context

    context = safe_get_context()
    if context is None:
        global _GLOBAL_SESSION
        if _GLOBAL_SESSION is not None:
            _GLOBAL_SESSION.reset()
        return

    session = getattr(context, "_r_session", None)
    if session is not None:
        session.reset()
