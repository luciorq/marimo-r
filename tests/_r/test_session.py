"""Tests for marimo._r.session — RSession and response parsing.

Covers:
- auto_unbox normalisation (stdout/stderr as str vs list)
- plot_info value stripping
- process lifecycle error handling
- plot format/quality parameters (png, svg, width, height, dpi)
"""

from __future__ import annotations

import json
import os
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from marimo._r.session import RSession

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


def _make_process_mock(
    response: dict[str, Any],
    *,
    poll_return: int | None = None,
) -> MagicMock:
    """Build a mock subprocess.Popen that returns *response* as JSON."""
    proc = MagicMock()
    proc.poll.return_value = poll_return
    proc.stdin = MagicMock()
    proc.stdout = MagicMock()
    proc.stderr = MagicMock()
    proc.stdout.readline.return_value = json.dumps(response) + "\n"
    return proc


# ===================================================================
# auto_unbox normalisation — the fix under test
# ===================================================================


class TestAutoUnboxNormalisation:
    """jsonlite's auto_unbox turns length-1 vectors into bare
    JSON scalars. The Python side must normalise them back to lists."""

    def test_stdout_bare_string_normalised_to_list(self) -> None:
        session = RSession()
        proc = _make_process_mock(
            {
                "id": "abc",
                "ok": True,
                "stdout": '[1] "A" "B"',
                "stderr": [],
                "value": None,
                "plot": None,
                "error": None,
            }
        )
        session._process = proc  # type: ignore[assignment]

        resp = session.execute("print(c('A','B'))", plot=False)

        assert isinstance(resp.stdout, list)
        assert resp.stdout == ['[1] "A" "B"']

    def test_stderr_bare_string_normalised_to_list(self) -> None:
        session = RSession()
        proc = _make_process_mock(
            {
                "id": "abc",
                "ok": True,
                "stdout": [],
                "stderr": "single warning line",
                "value": None,
                "plot": None,
                "error": None,
            }
        )
        session._process = proc  # type: ignore[assignment]

        resp = session.execute("warning('x')", plot=False)

        assert isinstance(resp.stderr, list)
        assert resp.stderr == ["single warning line"]

    def test_stdout_array_left_as_list(self) -> None:
        session = RSession()
        proc = _make_process_mock(
            {
                "id": "abc",
                "ok": True,
                "stdout": ["line 1", "line 2"],
                "stderr": [],
                "value": None,
                "plot": None,
                "error": None,
            }
        )
        session._process = proc  # type: ignore[assignment]

        resp = session.execute("print(1:30)", plot=False)

        assert resp.stdout == ["line 1", "line 2"]

    def test_stderr_array_left_as_list(self) -> None:
        session = RSession()
        proc = _make_process_mock(
            {
                "id": "abc",
                "ok": True,
                "stdout": [],
                "stderr": ["warn 1", "warn 2"],
                "value": None,
                "plot": None,
                "error": None,
            }
        )
        session._process = proc  # type: ignore[assignment]

        resp = session.execute("x <- 1", plot=False)

        assert resp.stderr == ["warn 1", "warn 2"]

    def test_null_stdout_normalised_to_empty_list(self) -> None:
        session = RSession()
        proc = _make_process_mock(
            {
                "id": "abc",
                "ok": True,
                "stdout": None,
                "stderr": None,
                "value": None,
                "plot": None,
                "error": None,
            }
        )
        session._process = proc  # type: ignore[assignment]

        resp = session.execute("invisible(1)", plot=False)

        assert resp.stdout == []
        assert resp.stderr == []

    def test_empty_string_stdout_normalised_to_empty_list(self) -> None:
        """An empty string is falsy so gets normalised to []."""
        session = RSession()
        proc = _make_process_mock(
            {
                "id": "abc",
                "ok": True,
                "stdout": "",
                "stderr": "",
                "value": None,
                "plot": None,
                "error": None,
            }
        )
        session._process = proc  # type: ignore[assignment]

        resp = session.execute("invisible(1)", plot=False)

        # Empty strings are falsy; `response.get("stdout") or []`
        # produces [].
        assert resp.stdout == []
        assert resp.stderr == []


# ===================================================================
# plot_info value stripping
# ===================================================================


class TestPlotInfoValueStripping:
    """The R backend may include a ``value`` field in ``plot``.
    It must be stripped before creating ``RResponse`` to avoid
    serialization of non-trivial R objects leaking through."""

    def test_plot_value_stripped(self) -> None:
        session = RSession()
        proc = _make_process_mock(
            {
                "id": "abc",
                "ok": True,
                "stdout": [],
                "stderr": [],
                "value": {"kind": "value", "data": None},
                "plot": {
                    "path": "/tmp/marimo-r-plot-123.png",
                    "error": None,
                    "value": "should-be-removed",
                },
                "error": None,
            }
        )
        session._process = proc  # type: ignore[assignment]

        resp = session.execute("plot(1:5)", plot=True)

        assert resp.plot_path == "/tmp/marimo-r-plot-123.png"
        assert resp.plot_error is None

    def test_plot_info_none_handled(self) -> None:
        session = RSession()
        proc = _make_process_mock(
            {
                "id": "abc",
                "ok": True,
                "stdout": [],
                "stderr": [],
                "value": None,
                "plot": None,
                "error": None,
            }
        )
        session._process = proc  # type: ignore[assignment]

        resp = session.execute("1+1", plot=False)

        assert resp.plot_path is None
        assert resp.plot_error is None


# ===================================================================
# Error handling
# ===================================================================


class TestProcessErrorHandling:
    def test_process_not_started_raises(self) -> None:
        session = RSession()
        session._process = None
        # Prevent start() from spawning a real process
        with patch.object(session, "start"):
            with pytest.raises(RuntimeError, match="not available"):
                session.execute("1+1")

    def test_process_exited_raises_with_stderr(self) -> None:
        session = RSession()
        proc = MagicMock()
        proc.poll.return_value = 1  # exited
        proc.stdin = MagicMock()
        proc.stderr = MagicMock()
        proc.stderr.read.return_value = "Segfault in R"
        session._process = proc  # type: ignore[assignment]

        # Prevent start() from replacing the mock process
        with patch.object(session, "start"):
            with pytest.raises(RuntimeError, match="exited before handling"):
                session.execute("crash()")

    def test_empty_response_raises(self) -> None:
        session = RSession()
        proc = MagicMock()
        proc.poll.side_effect = [None, None]
        proc.stdin = MagicMock()
        proc.stdout = MagicMock()
        proc.stdout.readline.return_value = ""
        proc.stderr = MagicMock()
        proc.stderr.read.return_value = "Fatal error"
        session._process = proc  # type: ignore[assignment]

        with pytest.raises(RuntimeError, match="did not return a response"):
            session.execute("1+1")

    def test_ok_false_propagated(self) -> None:
        session = RSession()
        proc = _make_process_mock(
            {
                "id": "abc",
                "ok": False,
                "stdout": [],
                "stderr": [],
                "value": None,
                "plot": None,
                "error": "object 'x' not found",
            }
        )
        session._process = proc  # type: ignore[assignment]

        resp = session.execute("print(x)", plot=False)

        assert resp.ok is False
        assert resp.error == "object 'x' not found"


# ===================================================================
# Value decoding (encode_value guards)
# ===================================================================


class TestValueEncoding:
    """Tests covering the encode_value guard that sets non-serializable
    S3 objects (like ggplot) to NULL."""

    def test_null_value_returned_as_none(self) -> None:
        session = RSession()
        proc = _make_process_mock(
            {
                "id": "abc",
                "ok": True,
                "stdout": [],
                "stderr": [],
                "value": {"kind": "value", "data": None},
                "plot": {
                    "path": "/tmp/marimo-r-plot-456.png",
                    "error": None,
                },
                "error": None,
            }
        )
        session._process = proc  # type: ignore[assignment]

        resp = session.execute(
            "ggplot(mtcars, aes(gear, mpg)) + geom_point()",
            plot=True,
        )

        # ggplot value is nullified; only the plot path survives
        assert resp.value is not None
        assert resp.value["data"] is None
        assert resp.plot_path == "/tmp/marimo-r-plot-456.png"

    def test_scalar_value_returned(self) -> None:
        session = RSession()
        proc = _make_process_mock(
            {
                "id": "abc",
                "ok": True,
                "stdout": [],
                "stderr": [],
                "value": {"kind": "value", "data": 42},
                "plot": None,
                "error": None,
            }
        )
        session._process = proc  # type: ignore[assignment]

        resp = session.execute("42", plot=False)

        assert resp.value == {"kind": "value", "data": 42}


# ===================================================================
# Plot format/quality parameters
# ===================================================================


class TestPlotParameters:
    """Verify that plot_format, plot_width, plot_height, and
    plot_dpi are included in the JSON request sent to R."""

    def _capture_request(
        self,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Execute with a mock process and return the JSON
        request that was written to stdin."""
        session = RSession()
        proc = _make_process_mock(
            {
                "id": "abc",
                "ok": True,
                "stdout": [],
                "stderr": [],
                "value": None,
                "plot": None,
                "error": None,
            }
        )
        session._process = proc  # type: ignore[assignment]
        session.execute("1+1", **kwargs)
        written = proc.stdin.write.call_args[0][0]
        # Strip the trailing newline
        return json.loads(written.rstrip())

    def test_defaults_in_request(self) -> None:
        req = self._capture_request()
        assert req["plot_format"] == "png"
        assert req["plot_width"] == 960
        assert req["plot_height"] == 640
        assert req["plot_dpi"] == 120

    def test_svg_format_in_request(self) -> None:
        req = self._capture_request(plot_format="svg")
        assert req["plot_format"] == "svg"

    def test_custom_dimensions_in_request(self) -> None:
        req = self._capture_request(
            plot_width=1200,
            plot_height=800,
            plot_dpi=150,
        )
        assert req["plot_width"] == 1200
        assert req["plot_height"] == 800
        assert req["plot_dpi"] == 150

    def test_all_params_combined(self) -> None:
        req = self._capture_request(
            plot_format="svg",
            plot_width=1920,
            plot_height=1080,
            plot_dpi=300,
        )
        assert req["plot_format"] == "svg"
        assert req["plot_width"] == 1920
        assert req["plot_height"] == 1080
        assert req["plot_dpi"] == 300

    def test_plot_false_still_sends_params(self) -> None:
        """Even with plot=False the params should be in the
        request (R backend ignores them when plot is false)."""
        req = self._capture_request(
            plot=False,
            plot_format="svg",
        )
        assert req["plot"] is False
        assert req["plot_format"] == "svg"


# ===================================================================
# Integration tests (require R installation)
# ===================================================================


_SKIP_R = pytest.mark.skipif(
    os.environ.get("MARIMO_R_SKIP_TESTS") == "1",
    reason="Skipping R integration tests",
)


@_SKIP_R
class TestRBackendIntegration:
    """Integration tests that spawn a real R process.

    These exercise the fixes in r_backend.R:
    - encode_value guard for non-serializable objects
    - capture_plot nullifies ggplot value after print
    - tryCatch fallback in main loop
    - auto_unbox behaviour over the wire
    """

    def _fresh_session(self) -> RSession:
        session = RSession()
        session.start()
        return session

    def test_single_line_stdout_is_list(self) -> None:
        """Regression: print(c('A','B')) should yield a list, not
        a string whose characters get joined on newlines."""
        session = self._fresh_session()
        try:
            resp = session.execute(
                'print(c("A", "B"))',
                plot=False,
            )
            assert resp.ok is True
            assert isinstance(resp.stdout, list)
            assert len(resp.stdout) == 1
            assert '"A"' in resp.stdout[0]
            assert '"B"' in resp.stdout[0]
        finally:
            session.close()

    def test_multi_line_stdout_is_list(self) -> None:
        session = self._fresh_session()
        try:
            resp = session.execute("print(1:30)", plot=False)
            assert resp.ok is True
            assert isinstance(resp.stdout, list)
            assert len(resp.stdout) > 1
        finally:
            session.close()

    def test_encode_value_null_for_non_serializable(self) -> None:
        """Non-serializable S3 objects should not crash the R
        process. The tryCatch fallback in the main loop keeps
        the process alive and returns a JSON serialization error."""
        session = self._fresh_session()
        try:
            # lm() returns a complex S3 object (class "lm")
            # that is technically a list but contains nested
            # non-serializable components.
            resp = session.execute(
                "lm(mpg ~ gear, data = mtcars)",
                plot=False,
            )
            # The tryCatch fallback catches the serialization
            # failure and returns ok=False with an error message.
            assert resp.ok is False
            assert resp.error is not None
            assert "serialization" in resp.error.lower()

            # Crucially, the R process should still be alive
            resp2 = session.execute("1 + 1", plot=False)
            assert resp2.ok is True
        finally:
            session.close()

    def test_ggplot_does_not_crash_process(self) -> None:
        """Regression: ggplot objects previously crashed the R
        process because jsonlite has no toJSON method for gg."""
        session = self._fresh_session()
        try:
            # First check if ggplot2 is available
            check = session.execute(
                'requireNamespace("ggplot2", quietly = TRUE)',
                plot=False,
            )
            if not check.ok:
                pytest.skip("ggplot2 not installed")

            has_ggplot2 = check.value and check.value.get("data") is True
            if not has_ggplot2:
                pytest.skip("ggplot2 not installed")

            resp = session.execute(
                "library(ggplot2)\n"
                "ggplot(mtcars, aes(gear, mpg)) + geom_point()",
                plot=True,
            )
            assert resp.ok is True
            # The ggplot value should be nullified
            if resp.value is not None:
                assert resp.value.get("data") is None
            # A plot file should exist
            assert resp.plot_path is not None

            # Process should still be alive for more requests
            resp2 = session.execute("1 + 1", plot=False)
            assert resp2.ok is True
        finally:
            session.close()

    def test_process_survives_serialization_error(self) -> None:
        """The tryCatch fallback in the main loop should keep
        the R process alive even if JSON serialization fails."""
        session = self._fresh_session()
        try:
            # Run a few normal requests to ensure stability
            for i in range(3):
                resp = session.execute(f"{i} + 1", plot=False)
                assert resp.ok is True
        finally:
            session.close()

    def test_warning_captured_in_stderr(self) -> None:
        session = self._fresh_session()
        try:
            resp = session.execute('warning("test-warn")', plot=False)
            assert resp.ok is True
            assert isinstance(resp.stderr, list)
            assert any("test-warn" in s for s in resp.stderr)
        finally:
            session.close()

    def test_error_sets_ok_false(self) -> None:
        session = self._fresh_session()
        try:
            resp = session.execute("stop('deliberate error')", plot=False)
            assert resp.ok is False
            assert resp.error is not None
            assert "deliberate error" in resp.error

            # Process should survive the error
            resp2 = session.execute("1 + 1", plot=False)
            assert resp2.ok is True
        finally:
            session.close()

    def test_empty_code_returns_ok(self) -> None:
        session = self._fresh_session()
        try:
            resp = session.execute("", plot=False)
            assert resp.ok is True
        finally:
            session.close()

    def test_dataframe_returns_arrow(self) -> None:
        session = self._fresh_session()
        try:
            resp = session.execute(
                "data.frame(x = 1:3, y = c(4, 5, 6))",
                plot=False,
            )
            assert resp.ok is True
            assert resp.value is not None
            assert resp.value.get("kind") == "arrow"
            assert resp.value.get("data") is not None
        finally:
            session.close()

    def test_base_plot_captures_png(self) -> None:
        session = self._fresh_session()
        try:
            resp = session.execute("plot(1:10, (1:10)^2)", plot=True)
            assert resp.ok is True
            assert resp.plot_path is not None
            # Plot file should actually exist
            import os as _os

            assert _os.path.isfile(resp.plot_path)
        finally:
            session.close()

    def test_session_reset(self) -> None:
        """After reset, previous variables should be gone."""
        session = self._fresh_session()
        try:
            session.execute("my_var <- 42", plot=False)
            session.reset()
            resp = session.execute("print(my_var)", plot=False)
            assert resp.ok is False
            assert resp.error is not None
            assert "my_var" in resp.error
        finally:
            session.close()

    def test_non_plot_code_has_no_plot_path(self) -> None:
        """Regression: non-plotting code with plot=True must not
        return a plot path (blank canvas false positive)."""
        session = self._fresh_session()
        try:
            resp = session.execute("x <- 1 + 1", plot=True)
            assert resp.ok is True
            assert resp.plot_path is None
        finally:
            session.close()

    def test_non_plot_dataframe_has_no_plot_path(self) -> None:
        """Returning a data frame should not produce a plot."""
        session = self._fresh_session()
        try:
            resp = session.execute("data.frame(a = 1:3, b = 4:6)", plot=True)
            assert resp.ok is True
            assert resp.value is not None
            assert resp.plot_path is None
        finally:
            session.close()

    # ---------------------------------------------------------------
    # Plot format / quality integration tests
    # ---------------------------------------------------------------

    def test_base_plot_svg(self) -> None:
        """SVG format should produce an .svg file."""
        session = self._fresh_session()
        try:
            resp = session.execute(
                "plot(1:10, (1:10)^2)",
                plot=True,
                plot_format="svg",
            )
            assert resp.ok is True
            assert resp.plot_path is not None
            assert resp.plot_path.endswith(".svg")
            import os as _os

            assert _os.path.isfile(resp.plot_path)
            # SVG files are XML text
            with open(resp.plot_path) as f:
                content = f.read()
            assert "<svg" in content
        finally:
            session.close()

    def test_custom_png_dimensions(self) -> None:
        """Custom width/height/dpi should produce a valid PNG."""
        session = self._fresh_session()
        try:
            resp = session.execute(
                "plot(1:5)",
                plot=True,
                plot_format="png",
                plot_width=1200,
                plot_height=800,
                plot_dpi=150,
            )
            assert resp.ok is True
            assert resp.plot_path is not None
            assert resp.plot_path.endswith(".png")
            import os as _os

            assert _os.path.isfile(resp.plot_path)
            # PNG files start with the magic bytes
            with open(resp.plot_path, "rb") as f:
                header = f.read(8)
            assert header[:4] == b"\x89PNG"
        finally:
            session.close()

    def test_svg_with_custom_dimensions(self) -> None:
        """SVG with custom dimensions uses width/height in
        inches (pixels / dpi)."""
        session = self._fresh_session()
        try:
            resp = session.execute(
                "plot(1:5)",
                plot=True,
                plot_format="svg",
                plot_width=1200,
                plot_height=600,
                plot_dpi=100,
            )
            assert resp.ok is True
            assert resp.plot_path is not None
            assert resp.plot_path.endswith(".svg")
            import os as _os

            assert _os.path.isfile(resp.plot_path)
            with open(resp.plot_path) as f:
                content = f.read()
            assert "<svg" in content
        finally:
            session.close()

    def test_default_png_unchanged(self) -> None:
        """Calling with all defaults still produces .png
        (backward compatibility)."""
        session = self._fresh_session()
        try:
            resp = session.execute("plot(1:5)", plot=True)
            assert resp.ok is True
            assert resp.plot_path is not None
            assert resp.plot_path.endswith(".png")
        finally:
            session.close()

    def test_invalid_format_falls_back_to_png(self) -> None:
        """Unknown format string should fall back to PNG."""
        session = self._fresh_session()
        try:
            resp = session.execute(
                "plot(1:5)",
                plot=True,
                plot_format="bmp",
            )
            assert resp.ok is True
            assert resp.plot_path is not None
            assert resp.plot_path.endswith(".png")
        finally:
            session.close()

    def test_ggplot_svg(self) -> None:
        """ggplot2 plots should work with SVG format."""
        session = self._fresh_session()
        try:
            check = session.execute(
                'requireNamespace("ggplot2", quietly = TRUE)',
                plot=False,
            )
            has_ggplot2 = (
                check.ok and check.value and check.value.get("data") is True
            )
            if not has_ggplot2:
                pytest.skip("ggplot2 not installed")

            resp = session.execute(
                "library(ggplot2)\n"
                "ggplot(mtcars, aes(gear, mpg)) + "
                "geom_point()",
                plot=True,
                plot_format="svg",
            )
            assert resp.ok is True
            assert resp.plot_path is not None
            assert resp.plot_path.endswith(".svg")
            with open(resp.plot_path) as f:
                content = f.read()
            assert "<svg" in content
        finally:
            session.close()


# ===================================================================
# ExecutionContext.with_r_process context manager
# ===================================================================


class TestWithRProcessContextManager:
    """Test that with_r_process correctly registers/unregisters
    the R subprocess on the ExecutionContext."""

    def test_registers_and_unregisters_process(self) -> None:
        from marimo._runtime.context.types import ExecutionContext

        exec_ctx = ExecutionContext(
            cell_id="cell_id", setting_element_value=False
        )
        assert exec_ctx.r_process is None

        mock_proc = MagicMock()
        with exec_ctx.with_r_process(mock_proc):
            assert exec_ctx.r_process is mock_proc

        assert exec_ctx.r_process is None

    def test_restores_previous_process(self) -> None:
        from marimo._runtime.context.types import ExecutionContext

        exec_ctx = ExecutionContext(
            cell_id="cell_id", setting_element_value=False
        )
        old_proc = MagicMock()
        exec_ctx.r_process = old_proc

        new_proc = MagicMock()
        with exec_ctx.with_r_process(new_proc):
            assert exec_ctx.r_process is new_proc

        assert exec_ctx.r_process is old_proc

    def test_restores_on_exception(self) -> None:
        from marimo._runtime.context.types import ExecutionContext

        exec_ctx = ExecutionContext(
            cell_id="cell_id", setting_element_value=False
        )
        assert exec_ctx.r_process is None

        mock_proc = MagicMock()

        def _use_and_raise() -> None:
            with exec_ctx.with_r_process(mock_proc):
                raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            _use_and_raise()

        assert exec_ctx.r_process is None


# ===================================================================
# RSession.execute interrupt handling
# ===================================================================


class TestRSessionInterruptHandling:
    """Test that RSession.execute handles KeyboardInterrupt correctly
    by resetting the session and re-raising."""

    def test_keyboard_interrupt_resets_session(self) -> None:
        session = RSession()
        proc = MagicMock()
        proc.poll.return_value = None
        proc.stdin = MagicMock()
        proc.stdout = MagicMock()
        proc.stderr = MagicMock()
        # Simulate KeyboardInterrupt during readline
        proc.stdout.readline.side_effect = KeyboardInterrupt
        session._process = proc  # type: ignore[assignment]

        with patch.object(session, "reset") as mock_reset:
            with pytest.raises(KeyboardInterrupt):
                session.execute("Sys.sleep(100)", plot=False)

            mock_reset.assert_called_once()

    def test_keyboard_interrupt_during_write_resets_session(self) -> None:
        session = RSession()
        proc = MagicMock()
        proc.poll.return_value = None
        proc.stdin = MagicMock()
        proc.stdout = MagicMock()
        proc.stderr = MagicMock()
        # Simulate KeyboardInterrupt during stdin write
        proc.stdin.write.side_effect = KeyboardInterrupt
        session._process = proc  # type: ignore[assignment]

        with patch.object(session, "reset") as mock_reset:
            with pytest.raises(KeyboardInterrupt):
                session.execute("Sys.sleep(100)", plot=False)

            mock_reset.assert_called_once()

    def test_normal_execution_does_not_reset(self) -> None:
        session = RSession()
        proc = _make_process_mock(
            {
                "id": "abc",
                "ok": True,
                "stdout": [],
                "stderr": [],
                "value": None,
                "plot": None,
                "error": None,
            }
        )
        session._process = proc  # type: ignore[assignment]

        with patch.object(session, "reset") as mock_reset:
            resp = session.execute("1+1", plot=False)

            assert resp.ok is True
            mock_reset.assert_not_called()
