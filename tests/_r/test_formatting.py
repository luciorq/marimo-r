# Copyright 2026 Marimo. All rights reserved.
"""Tests for the R formatters in marimo/_r/formatting.py."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from marimo._r.formatting import (
    AirFormatter,
    DefaultRFormatter,
    StylerFormatter,
)
from marimo._utils.formatter import CellCodes, Formatter

if TYPE_CHECKING:
    from pathlib import Path


class TestAirFormatter:
    """Test the AirFormatter class for R code formatting."""

    @patch("marimo._r.formatting._run_r_subprocess")
    @patch("marimo._r.launcher.find_r_tool")
    async def test_air_formats_over_stdin_with_config_anchor(
        self,
        mock_find_r_tool: MagicMock,
        mock_run: AsyncMock,
    ) -> None:
        """air runs over stdin, anchored at the notebook for air.toml discovery."""
        mock_find_r_tool.return_value = "/usr/bin/air"
        mock_run.return_value = (b"x <- 1\n", b"", 0)

        result = await AirFormatter(line_length=88).format(
            {"cell1": "x<-1"}, stdin_filename="/proj/nb.py"
        )

        assert result == {"cell1": "x <- 1"}
        args = mock_run.call_args.args
        assert args[0] == "/usr/bin/air"
        assert args[1] == "format"
        assert args[2] == "--stdin-file-path"
        # Anchored beside the notebook so air finds the project's air.toml.
        assert args[3] == "/proj/nb.R"
        assert mock_run.call_args.kwargs["input_data"] == b"x<-1"

    @patch("marimo._r.formatting._run_r_subprocess")
    @patch("marimo._r.launcher.find_r_tool")
    async def test_air_formatter_handles_errors_gracefully(
        self,
        mock_find_r_tool: MagicMock,
        mock_run: AsyncMock,
    ) -> None:
        """A failing cell is skipped rather than failing the batch."""
        mock_find_r_tool.return_value = "/usr/bin/air"
        mock_run.return_value = (b"", b"syntax error", 1)

        result = await AirFormatter(line_length=88).format({"cell1": "x<-1("})
        assert result == {}

    @patch("marimo._r.launcher.find_r_tool")
    async def test_air_formatter_raises_when_not_installed(
        self,
        mock_find_r_tool: MagicMock,
    ) -> None:
        mock_find_r_tool.return_value = None
        with pytest.raises(ModuleNotFoundError) as exc_info:
            await AirFormatter(line_length=88).format({"cell1": "x<-1"})
        assert exc_info.value.name == "air"


class TestStylerFormatter:
    """Test the StylerFormatter class."""

    @patch("marimo._r.formatting._run_r_subprocess")
    @patch("marimo._r.launcher.find_r_tool")
    async def test_styler_runs_with_a_matched_environment(
        self,
        mock_find_r_tool: MagicMock,
        mock_run: AsyncMock,
        tmp_path: Path,
    ) -> None:
        """The subprocess env must match the resolved R binary.

        Resolving a pixi R but inheriting an environment pointing at another
        R's library loads styler from the wrong tree — the bug class fixed
        repeatedly for the session and LSP.
        """
        prefix = tmp_path / ".pixi" / "envs" / "r"
        (prefix / "bin").mkdir(parents=True)
        (prefix / "bin" / "R").touch()
        (prefix / "lib" / "R" / "library").mkdir(parents=True)
        mock_find_r_tool.return_value = str(prefix / "bin" / "R")
        mock_run.return_value = (b"x <- 1\n", b"", 0)

        result = await StylerFormatter(line_length=88).format(
            {"cell1": "x<-1"}
        )

        assert result == {"cell1": "x <- 1"}
        env = mock_run.call_args.kwargs["env"]
        assert env is not None
        assert env["R_LIBS_USER"] == str(prefix / "lib" / "R" / "library")

    @patch("marimo._r.formatting._run_r_subprocess")
    @patch("marimo._r.launcher.find_r_tool")
    async def test_styler_formatter_handles_errors_gracefully(
        self,
        mock_find_r_tool: MagicMock,
        mock_run: AsyncMock,
    ) -> None:
        mock_find_r_tool.return_value = "/usr/bin/R"
        mock_run.return_value = (b"", b"no package called styler", 1)

        result = await StylerFormatter(line_length=88).format(
            {"cell1": "x<-1"}
        )
        assert result == {}

    @patch("marimo._r.launcher.find_r_tool")
    async def test_styler_formatter_raises_when_r_not_installed(
        self,
        mock_find_r_tool: MagicMock,
    ) -> None:
        mock_find_r_tool.return_value = None
        with pytest.raises(ModuleNotFoundError) as exc_info:
            await StylerFormatter(line_length=88).format({"cell1": "x<-1"})
        assert exc_info.value.name == "R"


class TestDefaultRFormatter:
    """Test the DefaultRFormatter class (air -> styler fallback)."""

    @patch("marimo._r.formatting.AirFormatter")
    @patch("marimo._r.launcher.find_r_tool")
    async def test_uses_air_when_available(
        self,
        mock_find_r_tool: MagicMock,
        mock_air_formatter: MagicMock,
    ) -> None:
        """Test DefaultRFormatter uses air when available."""
        mock_find_r_tool.return_value = "/usr/bin/air"

        mock_instance = AsyncMock()
        mock_air_formatter.return_value = mock_instance
        mock_instance.format.return_value = {"cell1": "formatted"}

        formatter = DefaultRFormatter(line_length=88)
        codes: CellCodes = {"cell1": "x<-1"}

        result = await formatter.format(codes)

        mock_air_formatter.assert_called_once_with(88)
        # stdin_filename is forwarded so air can anchor config discovery.
        mock_instance.format.assert_called_once_with(
            codes, stdin_filename=None
        )
        assert result == {"cell1": "formatted"}

    @patch("marimo._r.formatting.StylerFormatter")
    @patch("marimo._r.launcher.find_r_tool")
    async def test_uses_styler_when_air_unavailable(
        self,
        mock_find_r_tool: MagicMock,
        mock_styler_formatter: MagicMock,
    ) -> None:
        """Test DefaultRFormatter falls back to styler."""
        # air not available, R is available
        mock_find_r_tool.side_effect = lambda tool: (
            None if tool == "air" else "/usr/bin/R"
        )

        mock_instance = AsyncMock()
        mock_styler_formatter.return_value = mock_instance
        mock_instance.format.return_value = {"cell1": "formatted"}

        formatter = DefaultRFormatter(line_length=88)
        codes: CellCodes = {"cell1": "x<-1"}

        result = await formatter.format(codes)

        mock_styler_formatter.assert_called_once_with(88)
        mock_instance.format.assert_called_once_with(codes)
        assert result == {"cell1": "formatted"}

    @patch("marimo._r.launcher.find_r_tool")
    async def test_raises_when_no_r_formatters_available(
        self,
        mock_find_r_tool: MagicMock,
    ) -> None:
        """Test DefaultRFormatter raises when neither air nor R available."""
        mock_find_r_tool.return_value = None

        formatter = DefaultRFormatter(line_length=88)
        codes: CellCodes = {"cell1": "x<-1"}

        with pytest.raises(ModuleNotFoundError) as exc_info:
            await formatter.format(codes)

        assert "air" in str(exc_info.value)
        assert "styler" in str(exc_info.value)


class TestRFormatterIntegration:
    """Integration tests for R formatter classes."""

    async def test_r_formatter_inheritance_structure(
        self,
    ) -> None:
        """Test all R formatters inherit from Formatter."""
        assert issubclass(AirFormatter, Formatter)
        assert issubclass(StylerFormatter, Formatter)
        assert issubclass(DefaultRFormatter, Formatter)

    async def test_r_formatters_accept_line_length(
        self,
    ) -> None:
        """Test R formatters accept and store line_length."""
        formatters = [
            AirFormatter(100),
            StylerFormatter(100),
            DefaultRFormatter(100),
        ]
        for formatter in formatters:
            assert formatter.line_length == 100
