# Copyright 2026 Marimo. All rights reserved.
"""The R language servers, registered into marimo by `marimo._r.install`.

These live under `marimo/_r/` rather than in `marimo/_server/lsp.py` so that
the fork's delta in upstream files stays a set of one-line hooks. They plug
into `CompositeLspServer` through two generic mechanisms rather than
name-specific branches:

- `REQUIRES_CONFIG_READER = True` makes the composite pass its config reader to
  the constructor.
- `is_enabled_in(config)` (RJarlServer) lets a server compute its own enablement
  when it is not a plain top-level `language_servers.<id>.enabled` toggle —
  jarl's is nested under `r`, because it augments R support rather than being an
  alternative to it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Literal, Optional, cast

from marimo import _loggers
from marimo._config.config import MarimoConfig
from marimo._config.manager import MarimoConfigReader
from marimo._dependencies.dependencies import DependencyManager
from marimo._messaging.notification import AlertNotification
from marimo._server.lsp import BaseLspServer
from marimo._utils.paths import marimo_package_path

LOGGER = _loggers.marimo_logger()


class RLanguageServer(BaseLspServer):
    id = "r"
    # The composite passes its MarimoConfigReader to constructors that ask.
    REQUIRES_CONFIG_READER = True

    def __init__(self, port: int, config_reader: MarimoConfigReader) -> None:
        super().__init__(port)
        self.log_file = _loggers.get_log_directory() / "r-lsp.log"
        self._config_reader = config_reader
        self._languageserver_probe: Optional[bool] = None

    def _backend_preference(self) -> str:
        config = self._config_reader.get_config()
        backend = str(
            cast(Any, config.get("language_servers", {}))
            .get("r", {})
            .get("backend", "languageserver")
        )
        if backend == "air":
            # Air used to be selectable here. It only ever served formatting
            # requests, which marimo does not make over LSP — R cells are
            # formatted by marimo/_r/formatting.py running `air format` directly —
            # so the option started a server that answered nothing. Existing
            # configs keep working by falling through to the real one.
            LOGGER.info(
                "language_servers.r.backend = 'air' is no longer a language "
                "server backend; using 'languageserver'. Air still formats R "
                "cells."
            )
            return "languageserver"
        return backend

    def _tool_path(self, name: str) -> Optional[str]:
        """Resolve an R tool, preferring this workspace's pixi R environment.

        Plain `which` would search the marimo process's PATH, which does not
        include `.pixi/envs/r/bin` — so the LSP could analyse code with a
        different R installation than the one R cells actually execute in.
        """
        from marimo._r.launcher import find_r_tool

        return find_r_tool(name)

    def get_environment(self) -> Optional[dict[str, str]]:
        """Isolate the language server's R from the user's global R library.

        The `languageserver` backend really does start an R process —
        packages/lsp/index.ts runs `R --vanilla -e languageserver::run()` — and
        it inherits this environment through the node shim. `--vanilla` skips
        ~/.Renviron and ~/.Rprofile but not `R_LIBS`/`R_LIBS_USER`/
        `R_LIBS_SITE`, so without this the language server would analyse code
        against the user's global library while cells execute against the
        pinned pixi one: completions and diagnostics for packages that are not
        actually there, and none for packages that are.

        Applies the same policy as marimo/_r/launcher.py, including leaving a
        system R's configuration alone.
        """
        from marimo._r.launcher import build_environment

        r_path = self._tool_path("R")
        if r_path is None:
            return None
        env, _library = build_environment(Path(r_path))
        return env

    def _has_r(self) -> bool:
        return bool(self._tool_path("R"))

    def _has_languageserver(self) -> bool:
        """Whether the R we will actually launch can load `languageserver`.

        Cached for the lifetime of the process' run: this spawns R, and both
        `validate_requirements()` and `get_command()` need the answer. Only the
        former runs in a thread, so without the cache the `auto` path would pay
        a full R startup on the event loop.
        """
        if self._languageserver_probe is not None:
            return self._languageserver_probe

        # Computed into a local and published only on completion: the probe
        # takes up to 15s in a worker thread, and publishing a provisional
        # False first would let a concurrent reader (or a stop/start
        # interleaving) observe or overwrite a half-finished answer.
        probe = False
        r_path = self._tool_path("R")
        if r_path:
            try:
                result = subprocess.run(
                    [
                        r_path,
                        "--vanilla",
                        "-e",
                        "if (!requireNamespace('languageserver', quietly=TRUE)) quit(status=1)",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=15,
                    # Probe with the environment the server will really run in.
                    # Otherwise a languageserver installed only in the user's
                    # global library reads as present, and the server then
                    # starts with R_LIBS_* repointed at the pixi library and
                    # dies on `languageserver::run()`.
                    env=self.get_environment(),
                )
                probe = result.returncode == 0
            except Exception:
                probe = False
        self._languageserver_probe = probe
        return probe

    async def start(self) -> AlertNotification | None:
        if not DependencyManager.which("node"):
            LOGGER.info(
                "R LSP not available (node.js missing). Skipping LSP server."
            )
            return None
        if not self._has_r():
            LOGGER.info(
                "R LSP not available (R missing). Skipping LSP server."
            )
            return None
        return await super().start()

    def validate_requirements(self) -> str | Literal[True]:
        if self.process is None:
            # Fresh start: re-probe, since the user may have installed
            # languageserver or switched which R resolves. Keyed on start
            # rather than stop() because the composite tears children down
            # without calling subclass stop() overrides.
            self._languageserver_probe = None

        if not DependencyManager.which("node"):
            return (
                "node.js binary is missing. "
                "Install node at https://nodejs.org/."
            )

        # `auto` and `languageserver` are currently the same backend, so one
        # branch serves both — duplicating it had already cost the auto path
        # its "R is missing" case analysis.
        if not self._has_r():
            return "R is missing. Install R and the languageserver package."
        if not self._has_languageserver():
            return (
                "R languageserver is missing. Install it with "
                "`pixi add -f r r-languageserver`, or with "
                "install.packages('languageserver')."
            )
        return True

    def get_command(self) -> list[str]:
        lsp_bin = marimo_package_path() / "_lsp" / "index.cjs"
        if not lsp_bin.exists():
            LOGGER.debug("LSP binary not found at %s", lsp_bin)
            return []

        # packages/lsp/index.ts turns "languageserver:<path>" into the argv,
        # invoking that path as `R -e languageserver::run()` — so what we pass
        # is the R binary, not a languageserver executable.
        r_path = self._tool_path("R") or "R"
        return [
            "node",
            str(lsp_bin),
            "--port",
            str(self.port),
            "--lsp",
            f"languageserver:{r_path}",
            "--log-file",
            str(self.log_file),
        ]

    def missing_binary_alert(self) -> AlertNotification:
        return AlertNotification(
            title="R LSP: Connection Error",
            description="<span>Install the R <a class='hyperlink' href='https://github.com/REditorSupport/languageserver'>languageserver</a> package for R completions, hover, and diagnostics.</span>",
            variant="danger",
        )


class RJarlServer(BaseLspServer):
    """jarl, an R linter that speaks LSP.

    Deliberately not one of `RLanguageServer`'s backends. jarl provides
    diagnostics and quick fixes only — it advertises no completion or hover
    capability and rejects pull diagnostics outright — so it complements the
    real R language server rather than replacing it. The frontend federates the
    two, exactly as it federates several Python type checkers.

    Note that jarl publishes diagnostics in response to `textDocument/didSave`
    and nothing else; see NotebookLanguageServerClient, which sends one after
    edits settle so R cells get lint feedback while typing.
    """

    id = "r_jarl"
    REQUIRES_CONFIG_READER = True

    @classmethod
    def is_enabled_in(cls, config: MarimoConfig) -> bool:
        # Nested under `r` rather than top-level, because jarl augments the R
        # language server rather than being an alternative to it — disabling R
        # support disables jarl with it.
        r_config = cast(Any, config.get("language_servers", {})).get("r", {})
        return bool(
            r_config.get("enabled", False)
            and r_config.get("jarl", {}).get("enabled", False)
        )

    def __init__(self, port: int, config_reader: MarimoConfigReader) -> None:
        super().__init__(port)
        self.log_file = _loggers.get_log_directory() / "r-jarl-lsp.log"
        self._config_reader = config_reader

    def _binary(self) -> Optional[str]:
        from marimo._r.launcher import find_r_tool

        return find_r_tool("jarl")

    def validate_requirements(self) -> str | Literal[True]:
        if not DependencyManager.which("node"):
            return (
                "node.js binary is missing. "
                "Install node at https://nodejs.org/."
            )
        if not self._binary():
            return (
                "jarl is missing. Install it with `pixi add -f r jarl`, or "
                "from https://github.com/etiennebacher/jarl."
            )
        return True

    def get_command(self) -> list[str]:
        lsp_bin = marimo_package_path() / "_lsp" / "index.cjs"
        if not lsp_bin.exists():
            LOGGER.debug("LSP binary not found at %s", lsp_bin)
            return []
        return [
            "node",
            str(lsp_bin),
            "--port",
            str(self.port),
            "--lsp",
            f"jarl:{self._binary() or 'jarl'}",
            "--log-file",
            str(self.log_file),
        ]

    def missing_binary_alert(self) -> AlertNotification:
        return AlertNotification(
            title="R lint (jarl): Connection Error",
            description="<span>Install <a class='hyperlink' href='https://github.com/etiennebacher/jarl'>jarl</a> for R lint diagnostics.</span>",
            variant="danger",
        )


# Self-registration, at module bottom so both server classes exist whichever
# side of the lsp <-> lsp_servers cycle is imported first.
from marimo._r.install import register_lsp_servers  # noqa: E402

register_lsp_servers()
