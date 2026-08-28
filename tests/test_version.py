# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from typing import TYPE_CHECKING

from marimo import _version

if TYPE_CHECKING:
    import pytest


def test_get_version_from_this_distribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This fork ships as marimo-r, so that is tried first."""

    def get_version(distribution: str) -> str:
        assert distribution == _version.DISTRIBUTION_NAME == "marimo-r"
        return "1.2.3"

    monkeypatch.setattr(_version, "version", get_version)

    assert _version._get_version() == "1.2.3"


def test_get_version_from_upstream_marimo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running this source tree without the rename still reports a version."""

    def get_version(distribution: str) -> str:
        if distribution == "marimo":
            return "1.2.3"
        raise PackageNotFoundError(distribution)

    monkeypatch.setattr(_version, "version", get_version)

    assert _version._get_version() == "1.2.3"


def test_get_version_from_marimo_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get_version(distribution: str) -> str:
        if distribution == "marimo-base":
            return "1.2.3"
        raise PackageNotFoundError(distribution)

    monkeypatch.setattr(_version, "version", get_version)

    assert _version._get_version() == "1.2.3"


def test_get_version_when_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def get_version(distribution: str) -> str:
        raise PackageNotFoundError(distribution)

    monkeypatch.setattr(_version, "version", get_version)

    assert _version._get_version() == "unknown"
