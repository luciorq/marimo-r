# Copyright 2026 Marimo. All rights reserved.
"""Locate R and build an isolated environment for the R subprocess.

marimo can run against whatever R is on `PATH`, but this fork prefers a
pixi-managed R (the `r` environment declared in `pyproject.toml`), so that the R
toolchain and R packages are pinned in `pixi.lock` alongside everything else.

Two things are worth knowing about how this is done.

**We launch the pixi environment's R binary directly rather than shelling out to
`pixi run -e r R`.** `marimo/_runtime/handlers.py` interrupts a running R cell
with `r_proc.send_signal(SIGINT)` on the direct child PID. With `pixi run` in
between, that signal reaches pixi, not R, and the cell would not interrupt. The
binary inside `.pixi/envs/<env>` is the same R that `pixi run` would give us, so
resolving it ourselves costs nothing and keeps the process tree flat.

**`R --vanilla` is not enough to isolate the library path.** `--vanilla` stops R
from reading `~/.Renviron` and `~/.Rprofile`, but `R_LIBS`, `R_LIBS_USER`, and
`R_LIBS_SITE` are ordinary environment variables that survive it, and they are
prepended *ahead* of the environment's own library. A user with packages in
`~/R/x86_64-pc-linux-gnu-library/4.5` would silently shadow the pinned pixi
packages. `build_environment` closes that off.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from marimo import _loggers

LOGGER = _loggers.marimo_logger()

#: pixi environment holding the R toolchain. See `[tool.pixi.environments]`.
DEFAULT_PIXI_ENV = "r"

#: Explicit override; when set, this R is used and nothing is auto-detected.
R_BINARY_ENV_VAR = "MARIMO_R_BINARY"

#: Name of the pixi environment to look for.
R_PIXI_ENV_VAR = "MARIMO_R_PIXI_ENV"

#: Set to "0" to skip pixi discovery entirely and use R from `PATH`.
R_USE_PIXI_ENV_VAR = "MARIMO_R_USE_PIXI"

#: Passed to r_backend.R, which re-applies it with `.libPaths()`.
R_LIB_PATHS_ENV_VAR = "MARIMO_R_LIB_PATHS"

# Variables that can point R at libraries or startup files outside the
# environment we intend to use. All are cleared before R starts.
_LEAKY_R_VARS = (
    "R_LIBS",
    "R_LIBS_SITE",
    "R_LIBS_USER",
    # An inherited R_HOME from a *different* R installation makes the binary
    # load the wrong base packages, so let R compute its own.
    "R_HOME",
    "R_ENVIRON",
    "R_ENVIRON_USER",
    "R_PROFILE",
    "R_PROFILE_USER",
)


@dataclass(frozen=True)
class RInvocation:
    """How to start R: what to exec, and in what environment."""

    binary: str
    env: dict[str, str]
    #: Where the binary came from, for logging and error messages.
    source: str
    #: The pixi library directory R is pinned to, or None for a PATH R.
    library: Optional[str]

    @property
    def isolated(self) -> bool:
        return self.library is not None


def _is_truthy(value: Optional[str]) -> bool:
    return value is not None and value.strip().lower() not in (
        "",
        "0",
        "false",
        "no",
    )


def find_workspace_root(start: Optional[Path] = None) -> Optional[Path]:
    """Walk up from `start` looking for a pixi manifest.

    Cached per resolved start directory: this runs on the server event loop
    (LSP starts resolve several tools; every format request gates through it),
    and the walk reads pyproject.toml files all the way up. Like
    `_pixi_env_prefix`, the cache means a manifest created mid-session is not
    seen until process restart — `MARIMO_R_BINARY` is the escape hatch.
    """
    return _find_workspace_root_cached((start or Path.cwd()).resolve())


@lru_cache(maxsize=16)
def _find_workspace_root_cached(current: Path) -> Optional[Path]:
    for directory in (current, *current.parents):
        if (directory / "pixi.toml").is_file():
            return directory
        pyproject = directory / "pyproject.toml"
        if pyproject.is_file():
            try:
                if "[tool.pixi" in pyproject.read_text(encoding="utf-8"):
                    return directory
            except OSError:  # pragma: no cover - unreadable manifest
                continue
    return None


# Where conda-forge puts executables, across platforms. R itself lives under
# bin/ on unix; standalone tools like air and jarl land in Library/bin on
# Windows, which is *not* where R is.
_BIN_DIRS = (
    Path("bin"),
    Path("Scripts"),
    Path("Library") / "bin",
    Path("Lib") / "R" / "bin",
)


def _executable_in_prefix(prefix: Path, name: str) -> Optional[Path]:
    """Find an executable anywhere an environment prefix might keep one."""
    for relative in _BIN_DIRS:
        for candidate in (
            prefix / relative / name,
            prefix / relative / f"{name}.exe",
        ):
            if candidate.is_file():
                return candidate
    return None


@lru_cache(maxsize=8)
def _pixi_env_prefix(root: Path, env_name: str) -> Optional[Path]:
    """Resolve a pixi environment prefix.

    Deliberately filesystem-only. This runs during kernel startup (to report
    the `r_lsp` capability) and on the server's event loop, so it must not
    shell out. `pixi info --json` would additionally resolve
    `detached-environments` layouts, but it costs a subprocess on every call in
    any pixi project that has not installed this environment — which is the
    common case. `MARIMO_R_BINARY` is the escape hatch for those layouts.
    """
    conventional = root / ".pixi" / "envs" / env_name
    return conventional if conventional.is_dir() else None


def find_pixi_r_prefix(start: Optional[Path] = None) -> Optional[Path]:
    """Locate this workspace's pixi R environment, if there is one."""
    if not _is_truthy(os.environ.get(R_USE_PIXI_ENV_VAR, "1")):
        return None
    root = find_workspace_root(start)
    if root is None:
        return None
    env_name = os.environ.get(R_PIXI_ENV_VAR) or DEFAULT_PIXI_ENV
    return _pixi_env_prefix(root, env_name)


def find_pixi_r_binary(start: Optional[Path] = None) -> Optional[Path]:
    """Locate R inside this workspace's pixi R environment, if there is one."""
    prefix = find_pixi_r_prefix(start)
    if prefix is None:
        return None
    return _executable_in_prefix(prefix, "R")


def find_r_tool(name: str, start: Optional[Path] = None) -> Optional[str]:
    """Locate an R-ecosystem executable, preferring this workspace's pixi env.

    Used for the R language server binaries (`R`, `air`, `jarl`). Without this,
    `shutil.which` would resolve them from the marimo process's own PATH — the
    `default` pixi environment — and miss `.pixi/envs/r/bin` entirely. That
    matters beyond convenience: R cells would execute in the pixi R while the
    LSP analysed the code with a different R installation carrying a different
    set of packages, so completions and diagnostics would disagree with what
    actually runs.
    """
    if name == "R":
        override = os.environ.get(R_BINARY_ENV_VAR)
        if override:
            # An explicit override that does not exist reads as "R is
            # unavailable" rather than falling through to pixi/PATH (which
            # would silently ignore the user's explicit choice) or passing
            # gates only to fail deep inside a swallowed subprocess call.
            if Path(override).is_file():
                return override
            LOGGER.warning(
                "%s=%s does not exist; treating R as unavailable",
                R_BINARY_ENV_VAR,
                override,
            )
            return None

    # Search the whole prefix, not R's own directory: conda-forge installs
    # standalone tools like air and jarl beside R on unix but under
    # Library/bin on Windows.
    prefix = find_pixi_r_prefix(start)
    if prefix is not None:
        found = _executable_in_prefix(prefix, name)
        if found is not None:
            return str(found)

    return shutil.which(name)


def _library_for(binary: Path) -> Optional[str]:
    """The R library directory belonging to `binary`'s environment.

    Returns None when the binary is not inside an environment we manage, in
    which case we leave the user's own library configuration alone.
    """
    # <prefix>/bin/R -> <prefix>/lib/R/library
    prefix = binary.resolve().parent.parent
    if ".pixi" not in prefix.parts and not (prefix / "conda-meta").is_dir():
        return None
    for relative in (
        Path("lib") / "R" / "library",
        Path("Lib") / "R" / "library",
    ):
        library = prefix / relative
        if library.is_dir():
            return str(library)
    return None


def build_environment(
    binary: Path, base_env: Optional[dict[str, str]] = None
) -> tuple[dict[str, str], Optional[str]]:
    """Build the environment R should start in.

    Every variable that can inject an outside library is dropped. When `binary`
    belongs to a pixi environment, `R_LIBS_USER` and `R_LIBS_SITE` are then
    pinned to that environment's library — pinning matters as much as clearing,
    because R falls back to a per-user default like
    `~/R/x86_64-pc-linux-gnu-library/4.5` when `R_LIBS_USER` is unset.
    """
    env = dict(os.environ if base_env is None else base_env)

    library = _library_for(binary)
    if library is None:
        # Not an environment we manage — most likely a system R the user
        # installed themselves. Their R_LIBS_USER is where their packages live,
        # so leave the configuration entirely alone; stripping it here would
        # break `library(dplyr)` for anyone not using the pixi environment.
        return env, None

    for name in _LEAKY_R_VARS:
        env.pop(name, None)
    env["R_LIBS_USER"] = library
    env["R_LIBS_SITE"] = library
    # r_backend.R re-applies this with .libPaths() as a second line of
    # defence, in case anything in R's startup adds a path back.
    env[R_LIB_PATHS_ENV_VAR] = library
    return env, library


def resolve_r_invocation(
    start: Optional[Path] = None,
    base_env: Optional[dict[str, str]] = None,
) -> RInvocation:
    """Decide which R to run and how to run it.

    Resolution order: `MARIMO_R_BINARY`, then this workspace's pixi R
    environment, then `R` on `PATH`.
    """
    override = os.environ.get(R_BINARY_ENV_VAR)
    if override:
        binary, source = Path(override), R_BINARY_ENV_VAR
    else:
        pixi_binary = find_pixi_r_binary(start)
        if pixi_binary is not None:
            binary, source = pixi_binary, "pixi"
        else:
            found = shutil.which("R")
            # Fall back to the bare name so the caller still raises a
            # recognizable "R not found" error rather than one from here.
            binary, source = Path(found or "R"), "PATH"

    env, library = build_environment(binary, base_env)
    LOGGER.debug(
        "Using R at %s (source=%s, library=%s)", binary, source, library
    )
    return RInvocation(
        binary=str(binary), env=env, source=source, library=library
    )
