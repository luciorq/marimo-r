from __future__ import annotations

import os

import pytest

from marimo import r
from marimo._r.r import ARROW_FILE_THRESHOLD


@pytest.mark.skipif(
    os.environ.get("MARIMO_R_SKIP_TESTS") == "1",
    reason="Skipping R integration tests",
)
def test_r_returns_arrow_table() -> None:
    result = r(
        """
        df <- data.frame(x = 1:3, y = c(4, 5, 6))
        df
        """,
        output=False,
        plot=False,
    )
    assert result is not None
    assert hasattr(result, "schema")
    assert result.num_rows == 3


@pytest.mark.skipif(
    os.environ.get("MARIMO_R_SKIP_TESTS") == "1",
    reason="Skipping R integration tests",
)
def test_r_captures_plot() -> None:
    result = r(
        """
        plot(1:5, (1:5) ^ 2)
        """,
        output=True,
        plot=True,
    )
    assert result is None or hasattr(result, "schema")


@pytest.mark.skipif(
    os.environ.get("MARIMO_R_SKIP_TESTS") == "1",
    reason="Skipping R integration tests",
)
def test_r_captures_warning() -> None:
    result = r(
        """
        warning("marimo-r warning")
        """,
        output=False,
        plot=False,
    )
    assert result == "marimo-r warning"


@pytest.mark.skipif(
    os.environ.get("MARIMO_R_SKIP_TESTS") == "1",
    reason="Skipping R integration tests",
)
def test_r_large_dataframe_file_transfer() -> None:
    """Large R data frames should use file-based Arrow transfer."""
    # Generate enough rows so the Arrow IPC exceeds the threshold.
    # Each row has an integer + a character column (~50 bytes each),
    # so we need roughly threshold / 50 rows.
    n = ARROW_FILE_THRESHOLD // 20 + 1000
    result = r(
        f"""
        data.frame(
            x = seq_len({n}),
            y = paste0("row_", seq_len({n}))
        )
        """,
        output=False,
        plot=False,
    )
    assert result is not None
    assert hasattr(result, "schema")
    assert result.num_rows == n
    assert set(result.column_names) == {"x", "y"}


@pytest.mark.skipif(
    os.environ.get("MARIMO_R_SKIP_TESTS") == "1",
    reason="Skipping R integration tests",
)
def test_r_large_input_file_transfer() -> None:
    """Large Python->R inputs should use file-based Arrow transfer."""
    pa = pytest.importorskip("pyarrow")

    n = ARROW_FILE_THRESHOLD // 4 + 1000
    tbl = pa.table({"x": list(range(n))})
    result = r(
        """
        nrow(input_table)
        """,
        inputs={"input_table": tbl},
        output=False,
        plot=False,
    )
    assert result == n


@pytest.mark.skipif(
    os.environ.get("MARIMO_R_SKIP_TESTS") == "1",
    reason="Skipping R integration tests",
)
def test_r_duckdb_relation_as_input() -> None:
    """DuckDB relations should be transparently converted to Arrow
    for R input."""
    duckdb = pytest.importorskip("duckdb")

    rel = duckdb.sql("SELECT i AS x, i * 2 AS doubled FROM range(10) t(i)")
    result = r(
        """
        nrow(input_rel)
        """,
        inputs={"input_rel": rel},
        output=False,
        plot=False,
    )
    assert result == 10


@pytest.mark.skipif(
    os.environ.get("MARIMO_R_SKIP_TESTS") == "1",
    reason="Skipping R integration tests",
)
def test_r_duckdb_shared_connection() -> None:
    """When R has the duckdb package, Arrow inputs should be
    queryable via the shared DuckDB connection."""
    pa = pytest.importorskip("pyarrow")

    tbl = pa.table({"x": [1, 2, 3], "y": [10, 20, 30]})
    result = r(
        """
        if (exists(".marimo_duckdb", envir = environment())) {
            res <- DBI::dbGetQuery(
                .marimo_duckdb,
                "SELECT SUM(y) AS total FROM my_data"
            )
            res$total
        } else {
            # R duckdb package not available; fall back to Arrow sum
            sum(my_data$y)
        }
        """,
        inputs={"my_data": tbl},
        output=False,
        plot=False,
    )
    assert result == 60


@pytest.mark.skipif(
    os.environ.get("MARIMO_R_SKIP_TESTS") == "1",
    reason="Skipping R integration tests",
)
def test_pixi_r_ignores_the_user_global_library(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A user's R_LIBS* must not leak into the pixi-managed R subprocess.

    `R --vanilla` skips ~/.Renviron and ~/.Rprofile but honours R_LIBS,
    R_LIBS_USER, and R_LIBS_SITE, and prepends them ahead of the environment's
    own library — so an unpinned package in ~/R/... would silently shadow the
    version pinned in pixi.lock.
    """
    from marimo._r.launcher import resolve_r_invocation
    from marimo._r.r import _decode_value
    from marimo._r.session import RSession

    if not resolve_r_invocation().isolated:
        pytest.skip("R is not managed by pixi in this environment")

    monkeypatch.setenv("R_LIBS_USER", "/tmp/marimo-not-a-real-r-library")
    monkeypatch.setenv("R_LIBS", "/tmp/marimo-also-not-real")
    monkeypatch.setenv("R_HOME", "/tmp/marimo-wrong-r-home")

    session = RSession()
    try:
        response = session.execute(
            "paste(.libPaths(), collapse = .Platform$path.sep)",
            capture=False,
            plot=False,
        )
    finally:
        session.close()

    assert response.ok, response.error
    lib_paths = _decode_value(response)
    assert "not-a-real" not in lib_paths
    assert "also-not-real" not in lib_paths
    assert str(resolve_r_invocation().library) in lib_paths
