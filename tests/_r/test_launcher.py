# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

import os
from typing import TYPE_CHECKING
from unittest import mock

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from marimo._r.launcher import (
    R_BINARY_ENV_VAR,
    R_LIB_PATHS_ENV_VAR,
    R_USE_PIXI_ENV_VAR,
    _find_workspace_root_cached,
    _pixi_env_prefix,
    build_environment,
    find_pixi_r_binary,
    find_r_tool,
    find_workspace_root,
    resolve_r_invocation,
)

HOSTILE_ENV = {
    "R_LIBS": "/home/someone/R/site-library",
    "R_LIBS_USER": "/home/someone/R/x86_64-pc-linux-gnu-library/4.5",
    "R_LIBS_SITE": "/usr/local/lib/R/site-library",
    "R_HOME": "/usr/lib/R",
    "R_ENVIRON_USER": "/home/someone/.Renviron",
    "R_PROFILE_USER": "/home/someone/.Rprofile",
    "PATH": "/usr/bin",
}


@pytest.fixture(autouse=True)
def _clear_prefix_cache() -> None:
    _pixi_env_prefix.cache_clear()
    _find_workspace_root_cached.cache_clear()


def _make_pixi_env(root: Path, name: str = "r") -> Path:
    """Build a directory tree shaped like a pixi environment."""
    prefix = root / ".pixi" / "envs" / name
    (prefix / "bin").mkdir(parents=True)
    binary = prefix / "bin" / "R"
    binary.touch()
    (prefix / "lib" / "R" / "library").mkdir(parents=True)
    return binary


class TestWorkspaceDiscovery:
    def test_finds_pixi_toml(self, tmp_path: Path) -> None:
        (tmp_path / "pixi.toml").write_text("[workspace]\n")
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        assert find_workspace_root(nested) == tmp_path

    def test_finds_pyproject_with_pixi_table(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "x"\n\n[tool.pixi.workspace]\nchannels = []\n'
        )
        assert find_workspace_root(tmp_path) == tmp_path

    def test_ignores_pyproject_without_pixi(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
        assert find_workspace_root(tmp_path) is None

    def test_returns_none_when_no_manifest(self, tmp_path: Path) -> None:
        assert find_workspace_root(tmp_path) is None


class TestPixiBinaryDiscovery:
    def test_finds_binary_in_conventional_location(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "pixi.toml").write_text("[workspace]\n")
        binary = _make_pixi_env(tmp_path)
        with mock.patch.dict(os.environ, {}, clear=True):
            assert find_pixi_r_binary(tmp_path) == binary

    def test_honors_custom_environment_name(self, tmp_path: Path) -> None:
        (tmp_path / "pixi.toml").write_text("[workspace]\n")
        binary = _make_pixi_env(tmp_path, name="rstats")
        with mock.patch.dict(
            os.environ, {"MARIMO_R_PIXI_ENV": "rstats"}, clear=True
        ):
            assert find_pixi_r_binary(tmp_path) == binary

    def test_opt_out_disables_discovery(self, tmp_path: Path) -> None:
        (tmp_path / "pixi.toml").write_text("[workspace]\n")
        _make_pixi_env(tmp_path)
        with mock.patch.dict(
            os.environ, {R_USE_PIXI_ENV_VAR: "0"}, clear=True
        ):
            assert find_pixi_r_binary(tmp_path) is None

    def test_missing_environment_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "pixi.toml").write_text("[workspace]\n")
        with mock.patch.dict(os.environ, {}, clear=True):
            # No pixi on PATH, so the `pixi info` fallback cannot run either.
            with mock.patch("shutil.which", return_value=None):
                assert find_pixi_r_binary(tmp_path) is None


class TestEnvironmentIsolation:
    """`R --vanilla` ignores ~/.Renviron but not R_LIBS*, so we must."""

    def test_pixi_r_drops_every_leaky_variable(self, tmp_path: Path) -> None:
        binary = _make_pixi_env(tmp_path)
        env, library = build_environment(binary, HOSTILE_ENV)

        assert library == str(
            tmp_path / ".pixi" / "envs" / "r" / "lib" / "R" / "library"
        )
        for name in (
            "R_LIBS",
            "R_HOME",
            "R_ENVIRON_USER",
            "R_PROFILE_USER",
        ):
            assert name not in env, f"{name} should have been cleared"

    def test_pixi_r_pins_library_rather_than_only_clearing(
        self, tmp_path: Path
    ) -> None:
        # Clearing alone is not enough: with R_LIBS_USER unset, R falls back to
        # a per-user default like ~/R/x86_64-pc-linux-gnu-library/4.5.
        binary = _make_pixi_env(tmp_path)
        env, library = build_environment(binary, HOSTILE_ENV)
        assert env["R_LIBS_USER"] == library
        assert env["R_LIBS_SITE"] == library

    def test_pixi_r_passes_lib_paths_to_the_backend(
        self, tmp_path: Path
    ) -> None:
        binary = _make_pixi_env(tmp_path)
        env, library = build_environment(binary, HOSTILE_ENV)
        assert env[R_LIB_PATHS_ENV_VAR] == library

    def test_unrelated_variables_are_preserved(self, tmp_path: Path) -> None:
        binary = _make_pixi_env(tmp_path)
        env, _ = build_environment(binary, HOSTILE_ENV)
        assert env["PATH"] == "/usr/bin"

    def test_system_r_configuration_is_left_alone(
        self, tmp_path: Path
    ) -> None:
        # A system R is where the user's own library belongs; stripping
        # R_LIBS_USER there would break library() for non-pixi users.
        binary = tmp_path / "usr" / "bin" / "R"
        binary.parent.mkdir(parents=True)
        binary.touch()

        env, library = build_environment(binary, HOSTILE_ENV)
        assert library is None
        assert env == HOSTILE_ENV


class TestResolveInvocation:
    def test_explicit_binary_wins(self, tmp_path: Path) -> None:
        (tmp_path / "pixi.toml").write_text("[workspace]\n")
        _make_pixi_env(tmp_path)
        override = tmp_path / "custom-R"
        override.touch()

        with mock.patch.dict(
            os.environ, {R_BINARY_ENV_VAR: str(override)}, clear=True
        ):
            invocation = resolve_r_invocation(tmp_path)

        assert invocation.binary == str(override)
        assert invocation.source == R_BINARY_ENV_VAR

    def test_prefers_pixi_over_path(self, tmp_path: Path) -> None:
        (tmp_path / "pixi.toml").write_text("[workspace]\n")
        binary = _make_pixi_env(tmp_path)

        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("shutil.which", return_value="/usr/bin/R"):
                invocation = resolve_r_invocation(tmp_path)

        assert invocation.binary == str(binary)
        assert invocation.source == "pixi"
        assert invocation.isolated is True

    def test_falls_back_to_path(self, tmp_path: Path) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("shutil.which", return_value="/usr/bin/R"):
                invocation = resolve_r_invocation(tmp_path)

        assert invocation.binary == "/usr/bin/R"
        assert invocation.source == "PATH"
        assert invocation.isolated is False


class TestToolDiscovery:
    def test_finds_tools_beside_r(self, tmp_path: Path) -> None:
        (tmp_path / "pixi.toml").write_text("[workspace]\n")
        binary = _make_pixi_env(tmp_path)
        (binary.parent / "air").touch()
        (binary.parent / "jarl").touch()

        with mock.patch.dict(os.environ, {}, clear=True):
            assert find_r_tool("air", tmp_path) == str(binary.parent / "air")
            assert find_r_tool("jarl", tmp_path) == str(binary.parent / "jarl")

    def test_finds_tools_outside_r_own_directory(self, tmp_path: Path) -> None:
        # conda-forge puts standalone executables in Library/bin on Windows,
        # which is not where R lives — searching only R's directory misses them.
        (tmp_path / "pixi.toml").write_text("[workspace]\n")
        _make_pixi_env(tmp_path)
        library_bin = tmp_path / ".pixi" / "envs" / "r" / "Library" / "bin"
        library_bin.mkdir(parents=True)
        (library_bin / "air.exe").touch()

        with mock.patch.dict(os.environ, {}, clear=True):
            assert find_r_tool("air", tmp_path) == str(library_bin / "air.exe")

    def test_falls_back_to_path(self, tmp_path: Path) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("shutil.which", return_value="/usr/bin/air"):
                assert find_r_tool("air", tmp_path) == "/usr/bin/air"

    def test_discovery_never_shells_out(self, tmp_path: Path) -> None:
        """Runs at kernel startup and on the server event loop.

        `pixi info --json` would resolve detached-environments layouts but
        costs a subprocess on every call in a pixi project without the `r`
        environment installed, stalling both.
        """
        (tmp_path / "pixi.toml").write_text("[workspace]\n")  # no .pixi/envs
        _pixi_env_prefix.cache_clear()

        with (
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch("subprocess.run") as run,
            mock.patch("shutil.which", return_value=None),
        ):
            assert find_r_tool("R", tmp_path) is None

        run.assert_not_called()
