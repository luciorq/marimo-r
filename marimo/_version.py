# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

#: The distribution this package ships as.
#:
#: This fork publishes as `marimo-r` while keeping `marimo` as the *import*
#: name, so it is a drop-in replacement that cannot be co-installed with
#: upstream. Anywhere the distribution name is needed — version lookup, the
#: dependency a sandbox pins — use this rather than the literal "marimo", or
#: the sandbox will silently install upstream marimo and lose R support.
DISTRIBUTION_NAME = "marimo-r"

_DISTRIBUTIONS = (
    DISTRIBUTION_NAME,  # This fork
    "marimo",  # Upstream, if someone runs this source tree unrenamed
    "marimo-base",  # Slim distribution used by marimo.app
)


def _get_version() -> str:
    for distribution in _DISTRIBUTIONS:
        try:
            return version(distribution)
        except PackageNotFoundError:
            continue

    # package is not installed
    return "unknown"


__version__ = _get_version()
