# Copyright 2026 Marimo. All rights reserved.
"""Tests for the R language servers in marimo/_r/lsp_servers.py."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Optional, cast
from unittest import mock

import pytest

from marimo._config.manager import (
    MarimoConfigReader,
    MarimoConfigReaderWithOverrides,
)
from marimo._loggers import get_log_directory
from marimo._r.lsp_servers import RJarlServer, RLanguageServer
from marimo._server.lsp import CompositeLspServer, CopilotLspServer

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def mock_process() -> mock.MagicMock:
    process = mock.MagicMock()
    process.returncode = None
    return process


def test_r_server_uses_typed_format() -> None:
    config_reader = cast(
        MarimoConfigReader,
        MarimoConfigReaderWithOverrides(
            {
                "language_servers": {
                    "r": {"enabled": True, "backend": "languageserver"}
                },
                "experimental": {"lsp": True},
            }
        ),
    )
    server = RLanguageServer(port=8000, config_reader=config_reader)

    with (
        mock.patch(
            "marimo._r.lsp_servers.marimo_package_path",
            return_value=get_log_directory().parent,
        ),
        mock.patch("pathlib.Path.exists", return_value=True),
        _patch_r_tools(R="/usr/bin/R"),
    ):
        command = server.get_command()

    lsp_arg_index = command.index("--lsp")
    lsp_command = command[lsp_arg_index + 1]
    assert lsp_command == "languageserver:/usr/bin/R"


# ===================================================================
# RLanguageServer tests
# ===================================================================


def _make_r_server(backend: str = "languageserver") -> RLanguageServer:
    config_reader = cast(
        MarimoConfigReader,
        MarimoConfigReaderWithOverrides(
            {
                "language_servers": {
                    "r": {"enabled": True, "backend": backend}
                },
                "experimental": {"lsp": True},
            }
        ),
    )
    return RLanguageServer(port=8000, config_reader=config_reader)


def _patch_r_tools(**paths: Optional[str]) -> Any:
    """Patch R tool discovery.

    RLanguageServer resolves R, air, and jarl through
    `marimo._r.launcher.find_r_tool`, which looks inside the workspace's pixi
    `r` environment before falling back to PATH. Patching DependencyManager
    here would miss that seam and let the developer's real pixi environment
    decide the result, so these tests patch the seam itself.
    """
    return mock.patch(
        "marimo._r.launcher.find_r_tool",
        side_effect=lambda name: paths.get(name),
    )


def _patch_node(path: Optional[str] = "/usr/bin/node") -> Any:
    """Patch node discovery, which is not an R tool and still uses PATH."""
    return mock.patch(
        "marimo._dependencies.dependencies.DependencyManager.which",
        side_effect=lambda name: path if name == "node" else None,
    )


def test_r_backend_preference_defaults_to_languageserver() -> None:
    config_reader = cast(
        MarimoConfigReader,
        MarimoConfigReaderWithOverrides(
            {
                "language_servers": {"r": {"enabled": True}},
                "experimental": {"lsp": True},
            }
        ),
    )
    server = RLanguageServer(port=8000, config_reader=config_reader)
    assert server._backend_preference() == "languageserver"


def test_r_backend_preference_respects_config() -> None:
    server = _make_r_server("auto")
    assert server._backend_preference() == "auto"

    server = _make_r_server("languageserver")
    assert server._backend_preference() == "languageserver"


def test_r_has_r_detects_binary() -> None:
    server = _make_r_server()
    with _patch_r_tools(R="/usr/bin/R"):
        assert server._has_r() is True

    with _patch_r_tools():
        assert server._has_r() is False


def test_r_has_languageserver_checks_r_package() -> None:
    # A fresh server per scenario: the probe result is cached per instance, so
    # reusing one would just replay the first answer.
    with (
        _patch_r_tools(R="/usr/bin/R"),
        mock.patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = mock.MagicMock(returncode=0)
        assert _make_r_server()._has_languageserver() is True

    with (
        _patch_r_tools(R="/usr/bin/R"),
        mock.patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = mock.MagicMock(returncode=1)
        assert _make_r_server()._has_languageserver() is False

    with (
        _patch_r_tools(R="/usr/bin/R"),
        mock.patch("subprocess.run", side_effect=Exception("timeout")),
    ):
        assert _make_r_server()._has_languageserver() is False

    with _patch_r_tools():
        assert _make_r_server()._has_languageserver() is False


def test_r_has_languageserver_probes_the_resolved_r() -> None:
    """The package probe must run the same R that R cells will use."""
    server = _make_r_server()
    with (
        _patch_r_tools(R="/pixi/envs/r/bin/R"),
        mock.patch.object(server, "get_environment", return_value={}),
        mock.patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = mock.MagicMock(returncode=0)
        server._has_languageserver()

    assert mock_run.call_args[0][0][0] == "/pixi/envs/r/bin/R"


def test_r_validate_requirements_missing_node() -> None:
    server = _make_r_server()
    with _patch_node(None):
        result = server.validate_requirements()
        assert isinstance(result, str)
        assert "node.js" in result


def test_r_validate_requirements_languageserver_missing_r() -> None:
    server = _make_r_server("languageserver")
    with _patch_node(), _patch_r_tools():
        result = server.validate_requirements()
        assert isinstance(result, str)
        assert "R is missing" in result


def test_r_validate_requirements_languageserver_missing_languageserver() -> (
    None
):
    server = _make_r_server("languageserver")
    with (
        _patch_node(),
        _patch_r_tools(R="/usr/bin/R"),
        mock.patch.object(server, "_has_languageserver", return_value=False),
    ):
        result = server.validate_requirements()
        assert isinstance(result, str)
        assert "languageserver is missing" in result


def test_r_validate_requirements_languageserver_ok() -> None:
    server = _make_r_server("languageserver")
    with (
        _patch_node(),
        _patch_r_tools(R="/usr/bin/R"),
        mock.patch.object(server, "_has_languageserver", return_value=True),
    ):
        assert server.validate_requirements() is True


def test_r_validate_requirements_auto_prefers_languageserver() -> None:
    server = _make_r_server("auto")
    with (
        _patch_node(),
        _patch_r_tools(R="/usr/bin/R"),
        mock.patch.object(server, "_has_languageserver", return_value=True),
    ):
        assert server.validate_requirements() is True


def test_r_validate_requirements_auto_nothing_available() -> None:
    """With no R at all, say "R is missing" — not "languageserver is missing".

    The old auto fall-through duplicated the languageserver branch and lost
    its case analysis, telling users to install an R package they had no R
    to install into.
    """
    server = _make_r_server("auto")
    with (
        _patch_node(),
        _patch_r_tools(),
        mock.patch.object(server, "_has_languageserver", return_value=False),
    ):
        result = server.validate_requirements()
        assert isinstance(result, str)
        assert "R is missing" in result


def _lsp_arg(command: list[str]) -> str:
    return command[command.index("--lsp") + 1]


def _get_command(server: RLanguageServer, **tools: Optional[str]) -> list[str]:
    with (
        mock.patch(
            "marimo._r.lsp_servers.marimo_package_path",
            return_value=get_log_directory().parent,
        ),
        mock.patch("pathlib.Path.exists", return_value=True),
        _patch_r_tools(**tools),
    ):
        return server.get_command()


def test_r_get_command_languageserver_backend_passes_the_r_binary() -> None:
    # packages/lsp/index.ts invokes this path as `R -e languageserver::run()`,
    # so the path here is R itself, not a languageserver executable.
    command = _get_command(
        _make_r_server("languageserver"), R="/pixi/envs/r/bin/R"
    )
    assert _lsp_arg(command) == "languageserver:/pixi/envs/r/bin/R"


def test_r_get_command_auto_prefers_languageserver() -> None:
    server = _make_r_server("auto")
    with mock.patch.object(server, "_has_languageserver", return_value=True):
        command = _get_command(server, R="/usr/bin/R")
    assert _lsp_arg(command) == "languageserver:/usr/bin/R"


def test_r_get_command_no_lsp_binary() -> None:
    server = _make_r_server()
    with (
        mock.patch(
            "marimo._r.lsp_servers.marimo_package_path",
            return_value=get_log_directory().parent,
        ),
        mock.patch("pathlib.Path.exists", return_value=False),
    ):
        command = server.get_command()
    assert command == []


async def test_r_start_skips_without_node() -> None:
    server = _make_r_server()
    with _patch_node(None):
        assert await server.start() is None


async def test_r_start_skips_without_any_backend() -> None:
    server = _make_r_server()
    with _patch_node(), _patch_r_tools():
        assert await server.start() is None


async def test_r_start_delegates_to_super(
    mock_process: mock.MagicMock,
) -> None:
    server = _make_r_server()
    with (
        _patch_node(),
        _patch_r_tools(R="/usr/bin/R"),
        mock.patch.object(server, "_has_languageserver", return_value=True),
        mock.patch.object(server, "_wait_until_ready", return_value=True),
        mock.patch("subprocess.Popen", return_value=mock_process) as popen,
    ):
        result = await server.start()
        assert result is None
        popen.assert_called_once()


def test_r_missing_binary_alert() -> None:
    server = _make_r_server()
    alert = server.missing_binary_alert()
    assert alert.title == "R LSP: Connection Error"
    assert "languageserver" in alert.description
    assert alert.variant == "danger"


def test_r_lsp_environment_drops_the_user_global_library(
    tmp_path: Path,
) -> None:
    """The language server's R must not see the user's global R library.

    packages/lsp/index.ts starts the languageserver backend as
    `R --vanilla -e languageserver::run()`, inheriting this environment through
    the node shim. --vanilla ignores ~/.Renviron but not R_LIBS*, so without
    sanitizing here the language server would resolve packages against a
    different library than the one R cells execute against.
    """
    prefix = tmp_path / ".pixi" / "envs" / "r"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin" / "R").touch()
    (prefix / "lib" / "R" / "library").mkdir(parents=True)

    server = _make_r_server("languageserver")
    hostile = {
        "R_LIBS": "/home/someone/R/site-library",
        "R_LIBS_USER": "/home/someone/R/x86_64-pc-linux-gnu-library/4.5",
        "R_LIBS_SITE": "/usr/local/lib/R/site-library",
        "R_HOME": "/usr/lib/R",
        "PATH": "/usr/bin",
    }
    with (
        _patch_r_tools(R=str(prefix / "bin" / "R")),
        mock.patch.dict(os.environ, hostile, clear=True),
    ):
        env = server.get_environment()

    assert env is not None
    expected = str(prefix / "lib" / "R" / "library")
    assert env["R_LIBS_USER"] == expected
    assert env["R_LIBS_SITE"] == expected
    assert "R_LIBS" not in env
    assert "R_HOME" not in env
    # Unrelated variables still pass through.
    assert env["PATH"] == "/usr/bin"


def test_r_lsp_environment_leaves_a_system_r_alone() -> None:
    """A user's own R install is where their own library belongs."""
    server = _make_r_server("languageserver")
    with (
        _patch_r_tools(R="/usr/bin/R"),
        mock.patch.dict(
            os.environ, {"R_LIBS_USER": "/home/someone/R"}, clear=True
        ),
    ):
        env = server.get_environment()

    assert env is not None
    assert env["R_LIBS_USER"] == "/home/someone/R"


def test_non_r_servers_inherit_the_environment() -> None:
    """Only R needs sanitizing; everything else must be left untouched."""
    server = CopilotLspServer(port=8000)
    assert server.get_environment() is None


def test_r_languageserver_probe_uses_the_server_environment() -> None:
    """Detection and execution must see the same R library.

    Otherwise a languageserver installed only in the user's global library
    reads as present, and the server then starts with R_LIBS_* repointed at the
    pixi library and dies on `languageserver::run()`.
    """
    server = _make_r_server("languageserver")
    sentinel = {"R_LIBS_USER": "/pixi/lib", "PATH": "/usr/bin"}
    with (
        _patch_r_tools(R="/pixi/envs/r/bin/R"),
        mock.patch.object(server, "get_environment", return_value=sentinel),
        mock.patch("subprocess.run") as run,
    ):
        run.return_value = mock.MagicMock(returncode=0)
        assert server._has_languageserver() is True

    assert run.call_args.kwargs["env"] == sentinel


def test_r_languageserver_probe_is_cached_across_calls() -> None:
    """get_command() runs on the event loop; it must not respawn R."""
    server = _make_r_server("auto")
    with (
        _patch_r_tools(R="/usr/bin/R"),
        mock.patch.object(server, "get_environment", return_value={}),
        mock.patch("subprocess.run") as run,
    ):
        run.return_value = mock.MagicMock(returncode=0)
        server._has_languageserver()
        server._has_languageserver()
        server._has_languageserver()

    assert run.call_count == 1


def test_r_fresh_start_reprobes_languageserver() -> None:
    """A fresh start re-probes, so installing languageserver is picked up.

    Keyed on `process is None` in validate_requirements rather than a stop()
    override: the composite tears children down without calling subclass
    stop(), so stop-keyed invalidation was dead on that path.
    """
    server = _make_r_server("auto")
    server._languageserver_probe = False  # stale: user has since installed it
    with (
        _patch_node(),
        _patch_r_tools(R="/usr/bin/R"),
        mock.patch("subprocess.run") as run,
    ):
        run.return_value = mock.MagicMock(returncode=0)
        assert server.validate_requirements() is True
    run.assert_called_once()


def _make_jarl_server(
    r_enabled: bool = True, jarl_enabled: bool = True
) -> RJarlServer:
    config_reader = cast(
        MarimoConfigReader,
        MarimoConfigReaderWithOverrides(
            {
                "language_servers": {
                    "r": {
                        "enabled": r_enabled,
                        "jarl": {"enabled": jarl_enabled},
                    }
                },
                "experimental": {"lsp": True},
            }
        ),
    )
    return RJarlServer(port=8100, config_reader=config_reader)


def test_jarl_server_is_separate_from_the_r_backend() -> None:
    """jarl augments the R language server rather than replacing it."""
    assert RJarlServer.id == "r_jarl"
    assert RJarlServer.id != RLanguageServer.id
    assert "r_jarl" in CompositeLspServer.LANGUAGE_SERVERS


def test_jarl_get_command_uses_the_jarl_binary() -> None:
    server = _make_jarl_server()
    with (
        mock.patch(
            "marimo._r.lsp_servers.marimo_package_path",
            return_value=get_log_directory().parent,
        ),
        mock.patch("pathlib.Path.exists", return_value=True),
        _patch_r_tools(jarl="/pixi/envs/r/bin/jarl"),
    ):
        command = server.get_command()
    assert _lsp_arg(command) == "jarl:/pixi/envs/r/bin/jarl"


def test_jarl_validate_requirements() -> None:
    with _patch_node(), _patch_r_tools(jarl="/usr/bin/jarl"):
        assert _make_jarl_server().validate_requirements() is True

    with _patch_node(), _patch_r_tools():
        result = _make_jarl_server().validate_requirements()
        assert isinstance(result, str)
        assert "jarl is missing" in result

    with _patch_node(None), _patch_r_tools(jarl="/usr/bin/jarl"):
        result = _make_jarl_server().validate_requirements()
        assert isinstance(result, str)
        assert "node.js" in result


def test_jarl_does_not_sanitize_the_r_environment() -> None:
    """jarl is a Rust binary; it loads no R packages, so it inherits."""
    assert _make_jarl_server().get_environment() is None


@pytest.mark.parametrize(
    ("r_enabled", "jarl_enabled", "expected"),
    [
        (True, True, True),
        (True, False, False),
        # jarl is nested under `r`, so disabling R support disables it too.
        (False, True, False),
        (False, False, False),
    ],
)
def test_composite_enables_jarl_from_nested_config(
    r_enabled: bool, jarl_enabled: bool, expected: bool
) -> None:
    config_reader = cast(
        MarimoConfigReader,
        MarimoConfigReaderWithOverrides(
            {
                "language_servers": {
                    "r": {
                        "enabled": r_enabled,
                        "jarl": {"enabled": jarl_enabled},
                    }
                },
                "experimental": {"lsp": True},
            }
        ),
    )
    composite = CompositeLspServer(config_reader=config_reader, min_port=9000)
    config = config_reader.get_config()
    assert composite._is_enabled(config, "r_jarl") is expected


def test_composite_gives_r_and_jarl_distinct_ports() -> None:
    config_reader = cast(
        MarimoConfigReader,
        MarimoConfigReaderWithOverrides({"experimental": {"lsp": True}}),
    )
    composite = CompositeLspServer(config_reader=config_reader, min_port=9100)
    assert composite.servers["r"].port != composite.servers["r_jarl"].port, (
        "federated servers must not share a port"
    )


def test_r_legacy_air_backend_falls_through_to_languageserver() -> None:
    """`backend = "air"` was removed but must not break existing configs.

    Air only ever served formatting requests, which marimo does not make over
    LSP, so the option started a server that answered nothing. R cells are
    still formatted by air via marimo/_r/formatting.py.
    """
    server = _make_r_server("air")
    assert server._backend_preference() == "languageserver"

    with (
        _patch_node(),
        _patch_r_tools(R="/usr/bin/R"),
        mock.patch.object(server, "_has_languageserver", return_value=True),
    ):
        assert server.validate_requirements() is True

    command = _get_command(server, R="/usr/bin/R")
    assert _lsp_arg(command) == "languageserver:/usr/bin/R"
