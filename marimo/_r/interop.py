# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from marimo._dependencies.dependencies import DependencyManager

if TYPE_CHECKING:
    import duckdb  # type: ignore
    import polars as pl  # type: ignore
    import pyarrow as pa  # type: ignore


def bytes_to_arrow_table(payload: bytes) -> pa.Table:
    DependencyManager.require_many(
        "to decode Arrow payloads",
        DependencyManager.pyarrow,
        source="kernel",
    )
    import pyarrow as pa  # type: ignore

    return pa.ipc.open_stream(payload).read_all()


def arrow_to_duckdb_relation(
    table: pa.Table,
    *,
    connection: Optional[duckdb.DuckDBPyConnection] = None,
) -> duckdb.DuckDBPyRelation:
    DependencyManager.require_many(
        "to register Arrow tables with duckdb",
        DependencyManager.duckdb,
        DependencyManager.pyarrow,
        source="kernel",
    )
    import duckdb  # type: ignore

    conn = connection or duckdb.connect()
    return conn.from_arrow(table)


def arrow_to_polars(table: pa.Table) -> pl.DataFrame:
    DependencyManager.require_many(
        "to convert Arrow tables to polars",
        DependencyManager.polars,
        DependencyManager.pyarrow,
        source="kernel",
    )
    import polars as pl  # type: ignore

    result = pl.from_arrow(table)
    assert isinstance(result, pl.DataFrame)
    return result


def duckdb_relation_to_arrow(
    relation: duckdb.DuckDBPyRelation,
) -> pa.Table:
    """Convert a DuckDB relation to a PyArrow table.

    This is used by ``_encode_inputs`` to serialize DuckDB query
    results for transfer to R.
    """
    DependencyManager.require_many(
        "to convert DuckDB relations to Arrow",
        DependencyManager.duckdb,
        DependencyManager.pyarrow,
        source="kernel",
    )
    return relation.fetch_arrow_table()
