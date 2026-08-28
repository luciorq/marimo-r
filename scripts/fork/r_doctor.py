# Copyright 2026 Marimo. All rights reserved.
"""Report what R support will actually do before you test it by hand.

Manual testing of R support is slow and the interesting failures are invisible
from the UI. The R cell can execute against one R while the language server
analyses against another; the server can start perfectly and the frontend still
refuse to attach because a capability says R is unavailable. Clicking around a
notebook tells you *that* something is wrong, never *which* of those it is.

Run `pixi run r-doctor` before a manual session, and again whenever R behaves
oddly. Exits non-zero when it finds a real inconsistency, so it also works as a
gate in a script.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional, cast

# Import through the same paths the app uses, so this reports what marimo will
# really do rather than a re-implementation of it.
from marimo._config.manager import MarimoConfigReaderWithOverrides
from marimo._messaging.notification import KernelCapabilitiesNotification
from marimo._r.launcher import find_r_tool, resolve_r_invocation
from marimo._r.lsp_servers import RLanguageServer
from marimo._utils.paths import marimo_package_path

GREEN, YELLOW, RED, DIM, RESET = (
    "\033[32m",
    "\033[33m",
    "\033[31m",
    "\033[2m",
    "\033[0m",
)

problems: list[str] = []
warnings: list[str] = []


def section(title: str) -> None:
    print(f"\n{title}\n{'─' * len(title)}")


def row(label: str, value: object, ok: Optional[bool] = None) -> None:
    mark = "" if ok is None else (f"{GREEN}✓{RESET} " if ok else f"{RED}✗{RESET} ")
    print(f"  {mark}{label:<26} {value}")


def r_backend() -> str:
    return cast(str, os.environ.get("MARIMO_R_BACKEND", "languageserver"))


def main() -> int:
    section("R runtime (used by R cells)")
    invocation = resolve_r_invocation()
    row("binary", invocation.binary, Path(invocation.binary).is_file())
    row("resolved from", invocation.source)
    row("library isolated", invocation.isolated, invocation.isolated)
    row("library", invocation.library or f"{DIM}(user's own){RESET}")

    if not Path(invocation.binary).is_file():
        problems.append(
            f"R was not found at {invocation.binary}. Run `pixi install -e r`."
        )
        _summary()
        return 1

    # Ask R itself rather than trusting the environment we built for it.
    probe = subprocess.run(
        [
            invocation.binary,
            "--vanilla",
            "--slave",
            "-e",
            'cat(paste(.libPaths(), collapse="\\n"))',
        ],
        env=invocation.env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    lib_paths = [p for p in probe.stdout.splitlines() if p.strip()]
    section("Library path, as R reports it")
    for path in lib_paths:
        mine = invocation.library is not None and path == invocation.library
        row("", path, None if invocation.library is None else mine)
    if invocation.isolated and lib_paths != [invocation.library]:
        problems.append(
            "R sees libraries outside the pixi environment. A package there "
            "will shadow the pinned one. Check R_LIBS/R_LIBS_USER/R_LIBS_SITE."
        )

    section("R packages")
    packages = subprocess.run(
        [
            invocation.binary,
            "--vanilla",
            "--slave",
            "-e",
            'for (p in c("jsonlite","arrow","DBI","duckdb","ggplot2",'
            '"languageserver","lintr","styler")) '
            'cat(p, as.character(requireNamespace(p, quietly=TRUE)), "\\n")',
        ],
        env=invocation.env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    required = {"jsonlite", "arrow"}
    for line in packages.stdout.splitlines():
        name, _, present = line.partition(" ")
        if not name:
            continue
        have = present.strip() == "TRUE"
        row(name, "installed" if have else "missing", have)
        if not have and name in required:
            problems.append(
                f"R package {name!r} is required by r_backend.R. "
                "Run `pixi install -e r`."
            )

    section("Language server")
    config = cast(
        Any,
        MarimoConfigReaderWithOverrides(
            {
                "language_servers": {
                    "r": {"enabled": True, "backend": r_backend()}
                },
                "experimental": {"lsp": True},
            }
        ),
    )
    server = RLanguageServer(port=8000, config_reader=config)

    for tool in ("R", "jarl"):
        found = find_r_tool(tool)
        row(tool, found or f"{DIM}not found{RESET}", bool(found))

    row("languageserver package", server._has_languageserver(), None)
    row("configured backend", r_backend())

    validation = server.validate_requirements()
    row("requirements", validation, validation is True)
    if validation is not True:
        problems.append(str(validation))

    lsp_bundle = marimo_package_path() / "_lsp" / "index.cjs"
    row("lsp bundle built", lsp_bundle.is_file(), lsp_bundle.is_file())
    if not lsp_bundle.is_file():
        problems.append(
            f"{lsp_bundle} is missing, so the R LSP silently will not start "
            "(get_command returns []). Run `pixi run build`."
        )

    command = server.get_command()
    if command:
        spec = command[command.index("--lsp") + 1]
        row("would launch", spec)

    # air is not a language server backend: marimo never sends LSP formatting
    # requests, and R cells are formatted by marimo/_r/formatting.py running
    # `air format`. Reported here because a missing air breaks formatting.
    section("R formatting")
    air = find_r_tool("air")
    row("air", air or f"{DIM}not found{RESET}", bool(air))
    if not air:
        warnings.append(
            "air is missing, so formatting an R cell will fail. Install it "
            "with `pixi add -f r air`."
        )

    # The frontend gates on this. A server that starts while this is False is
    # the failure mode that unit tests do not catch.
    section("Frontend capability (what the editor checks)")
    capability = KernelCapabilitiesNotification().r_lsp
    row("r_lsp", capability, capability)
    if not capability and find_r_tool("R"):
        problems.append(
            "R is available but the r_lsp capability is False, so the editor "
            "will not attach the LSP client no matter how healthy the server "
            "is. Check node is installed and see marimo/_messaging/notification.py."
        )
    if capability and validation is not True:
        warnings.append(
            "The editor will try to attach an LSP client, but the server "
            "cannot start. Expect a connection error banner."
        )

    _summary()
    return 1 if problems else 0


def _summary() -> None:
    section("Summary")
    for problem in problems:
        print(f"  {RED}✗{RESET} {problem}")
    for warning in warnings:
        print(f"  {YELLOW}!{RESET} {warning}")
    if not problems and not warnings:
        print(f"  {GREEN}✓{RESET} R support looks ready for manual testing.")
    print()


if __name__ == "__main__":
    sys.exit(main())
