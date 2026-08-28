# Copyright 2026 Marimo. All rights reserved.
"""R code formatters, dispatched from the format endpoint for R cells.

These live under `marimo/_r/` so that `marimo/_utils/formatter.py` stays
byte-identical to upstream. `DefaultRFormatter` mirrors `DefaultFormatter`
(which tries ruff, then black): it tries air, then styler, then raises.

Subprocesses run with the environment from `launcher.build_environment`, so the
R binary and the library it loads are always a matched pair — resolving a pixi
R but inheriting an environment that points at another R's library would load
(or fail to find) packages from the wrong tree.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Optional

from marimo import _loggers
from marimo._utils.formatter import CellCodes, FormatError, Formatter

LOGGER = _loggers.marimo_logger()


async def _run_r_subprocess(
    *args: str,
    input_data: bytes,
    env: Optional[dict[str, str]] = None,
) -> tuple[bytes, bytes, int]:
    """Run an R-toolchain subprocess with an explicit environment.

    Not `marimo._utils.formatter._run_subprocess_safe`: that helper is private
    to an upstream file this fork keeps byte-identical, so depending on it
    would turn an upstream rename into an import error here — and it cannot
    pass `env`, which the matched-pair guarantee above requires.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await process.communicate(input_data)
        return stdout, stderr, process.returncode or 0
    except NotImplementedError:
        # Windows may lack subprocess support on the running event loop.
        def run_sync() -> tuple[bytes, bytes, int]:
            result = subprocess.run(
                args,
                input=input_data,
                capture_output=True,
                timeout=60,
                env=env,
            )
            return result.stdout, result.stderr, result.returncode

        return await asyncio.to_thread(run_sync)


class AirFormatter(Formatter):
    """Format R code using the air CLI tool.

    Runs `air format --stdin-file-path <path>` over stdin/stdout. The path
    does not need to exist; air uses it to anchor its upward search for the
    project's `air.toml`, so formatting a cell honours the same configuration
    as running air in the project would. Air has no line-width CLI flag —
    width comes from `air.toml` — so `line_length` is accepted for signature
    compatibility but not forwarded.
    """

    async def format(
        self, codes: CellCodes, stdin_filename: str | None = None
    ) -> CellCodes:
        from marimo._r.launcher import build_environment, find_r_tool

        air_path = find_r_tool("air")
        if not air_path:
            raise ModuleNotFoundError(
                "air is not installed",
                name="air",
            )
        env, _library = build_environment(Path(air_path))

        # Anchor air's config discovery at the notebook when known, else the
        # server's working directory (the project root in the common case).
        anchor = str(
            Path(stdin_filename).with_suffix(".R")
            if stdin_filename
            else Path.cwd() / "notebook.R"
        )

        formatted_codes: CellCodes = {}
        for key, code in codes.items():
            try:
                stdout, stderr, returncode = await _run_r_subprocess(
                    air_path,
                    "format",
                    "--stdin-file-path",
                    anchor,
                    input_data=code.encode("utf-8"),
                    env=env,
                )
                if returncode != 0:
                    raise FormatError(
                        f"air formatting failed: "
                        f"{stderr.decode(errors='replace')}"
                    )
                formatted_codes[key] = stdout.decode("utf-8").strip()
            except Exception as e:
                LOGGER.error("Failed to format R code with air")
                LOGGER.debug(e)
                continue
        return formatted_codes


class StylerFormatter(Formatter):
    """Format R code using the styler R package via R subprocess."""

    async def format(
        self, codes: CellCodes, stdin_filename: str | None = None
    ) -> CellCodes:
        del stdin_filename
        from marimo._r.launcher import build_environment, find_r_tool

        r_path = find_r_tool("R")
        if not r_path:
            raise ModuleNotFoundError(
                "R is not installed",
                name="R",
            )
        # The environment matching this R binary: for a managed (pixi/conda) R
        # the library is pinned to its own tree, so styler is looked up where
        # this R's packages actually live rather than wherever inherited
        # R_LIBS_* variables happen to point.
        env, _library = build_environment(Path(r_path))

        formatted_codes: CellCodes = {}
        for key, code in codes.items():
            try:
                formatted_codes[key] = await self._format_one(
                    r_path, code, env
                )
            except Exception as e:
                LOGGER.error(
                    "Failed to format R code with styler. If styler is not "
                    "installed for this R, install it with "
                    "`pixi add -f r r-styler` (pixi) or "
                    "install.packages('styler')."
                )
                LOGGER.debug(e)
                continue
        return formatted_codes

    @staticmethod
    async def _format_one(
        r_path: str, code: str, env: Optional[dict[str, str]]
    ) -> str:
        r_expr = 'cat(styler::style_text(readLines("stdin")), sep="\\n")'
        stdout, stderr, returncode = await _run_r_subprocess(
            r_path,
            "--vanilla",
            "--slave",
            "-e",
            r_expr,
            input_data=code.encode("utf-8"),
            env=env,
        )
        if returncode != 0:
            err = stderr.decode(errors="replace")
            raise FormatError(f"styler formatting failed: {err}")
        return stdout.decode("utf-8").strip()


class DefaultRFormatter(Formatter):
    """Try air, then styler, then raise.

    Mirrors DefaultFormatter which tries ruff then black.
    """

    async def format(
        self, codes: CellCodes, stdin_filename: str | None = None
    ) -> CellCodes:
        # Gated on find_r_tool, matching how the formatters themselves resolve
        # their binaries: a plain PATH lookup misses the pixi `r` environment,
        # so a pixi-only air would skip the air path entirely.
        from marimo._r.launcher import find_r_tool

        if find_r_tool("air"):
            return await AirFormatter(self.line_length).format(
                codes, stdin_filename=stdin_filename
            )
        elif find_r_tool("R"):
            return await StylerFormatter(self.line_length).format(codes)
        else:
            raise ModuleNotFoundError(
                "To enable R code formatting, "
                "please install air or the styler R package",
                name="air",
            )
