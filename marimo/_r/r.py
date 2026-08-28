# Copyright 2026 Marimo. All rights reserved.
from __future__ import annotations

import base64
import tempfile
from typing import Any, Optional

from marimo._output.rich_help import mddoc
from marimo._plugins.stateless.flex import vstack
from marimo._plugins.stateless.image import image
from marimo._plugins.stateless.plain_text import plain_text
from marimo._plugins.ui._impl.table import table
from marimo._r.interop import bytes_to_arrow_table
from marimo._r.session import (
    RResponse,
    get_session,
    reset_session as reset_r_session,
)
from marimo._runtime.output import replace

# Payloads larger than this are written to a temp file instead of
# being inlined as base64 in the JSON message.  This avoids
# doubling memory for large tables (base64 is ~1.33x, but JSON
# escaping and Python string overhead add up).
ARROW_FILE_THRESHOLD = 1_048_576  # 1 MB


def _encode_inputs(values: dict[str, Any]) -> dict[str, Any]:
    encoded: dict[str, Any] = {}
    for key, value in values.items():
        table_value = _as_arrow_table(value)
        if table_value is not None:
            arrow_bytes = _arrow_to_bytes(table_value)
            if len(arrow_bytes) >= ARROW_FILE_THRESHOLD:
                # Large payload: write to temp file for zero-copy
                encoded[key] = _write_arrow_file(arrow_bytes)
            else:
                encoded[key] = {
                    "type": "arrow",
                    "data": base64.b64encode(arrow_bytes).decode("ascii"),
                }
        else:
            encoded[key] = {
                "type": "value",
                "data": _json_compatible(value),
            }
    return encoded


def _write_arrow_file(arrow_bytes: bytes) -> dict[str, Any]:
    """Write Arrow IPC bytes to a temp file and return the payload
    descriptor.  The reader (R or Python) is responsible for
    deleting the file after consuming it."""
    fd, path = tempfile.mkstemp(prefix="marimo-arrow-", suffix=".ipc")
    try:
        with open(fd, "wb") as f:
            f.write(arrow_bytes)
    except BaseException:
        import os

        os.close(fd)
        os.unlink(path)
        raise
    return {"type": "arrow_file", "path": path}


def _json_compatible(value: Any) -> Any:
    """Convert a Python value into something JSON-serializable.

    Handles numpy arrays/scalars, datetime objects, sets, bytes,
    and raises a clear error for types that cannot be converted.
    """
    # Fast path: JSON-native types
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_compatible(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_compatible(v) for k, v in value.items()}

    # Sets -> sorted lists
    if isinstance(value, (set, frozenset)):
        return sorted(_json_compatible(v) for v in value)

    # bytes -> base64 string
    if isinstance(value, (bytes, bytearray)):
        return base64.b64encode(value).decode("ascii")

    # numpy scalars and arrays
    try:
        import numpy as np  # type: ignore

        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return float(value)
        if isinstance(value, np.bool_):
            return bool(value)
        if isinstance(value, np.ndarray):
            return value.tolist()
    except ImportError:
        pass

    # datetime objects
    try:
        import datetime

        if isinstance(value, datetime.datetime):
            return value.isoformat()
        if isinstance(value, datetime.date):
            return value.isoformat()
        if isinstance(value, datetime.time):
            return value.isoformat()
    except Exception:
        pass

    # polars LazyFrame — explicit error
    try:
        import polars as pl  # type: ignore

        if isinstance(value, pl.LazyFrame):
            raise TypeError(
                "Cannot pass a polars LazyFrame to R. "
                "Call .collect() first to get a DataFrame."
            )
    except ImportError:
        pass

    # DuckDB relations should go through _as_arrow_table, not here
    try:
        import duckdb  # type: ignore

        if isinstance(value, duckdb.DuckDBPyRelation):
            raise TypeError(
                "Cannot JSON-serialize a DuckDB relation. "
                "It should be converted to Arrow via "
                "_as_arrow_table() first."
            )
    except ImportError:
        pass

    # Fallback: let json.dumps handle it (will raise TypeError
    # for truly unsupported types with a clear traceback)
    return value


def _as_arrow_table(value: Any) -> Any:
    try:
        import pyarrow as pa  # type: ignore
    except Exception:
        return None

    if isinstance(value, pa.Table):
        return value
    if isinstance(value, pa.RecordBatch):
        return pa.Table.from_batches([value])
    # DuckDB relations: fetch_arrow_table() returns a pyarrow.Table
    fetch_arrow = getattr(value, "fetch_arrow_table", None)
    if callable(fetch_arrow):
        try:
            return fetch_arrow()
        except Exception:
            return None
    to_arrow = getattr(value, "to_arrow", None)
    if callable(to_arrow):
        try:
            return to_arrow()
        except Exception:
            return None
    try:
        import pandas as pd  # type: ignore

        if isinstance(value, pd.DataFrame):
            return pa.Table.from_pandas(value)
    except Exception:
        return None
    return None


def _arrow_to_bytes(table_value: Any) -> bytes:
    import pyarrow as pa  # type: ignore

    sink = pa.BufferOutputStream()
    # Use LZ4 compression when available to reduce transfer size.
    # The R arrow package can read LZ4-compressed IPC transparently.
    try:
        options = pa.ipc.IpcWriteOptions(compression="lz4")
    except Exception:
        options = None
    writer_kwargs: dict[str, Any] = {}
    if options is not None:
        writer_kwargs["options"] = options
    with pa.ipc.new_stream(
        sink, table_value.schema, **writer_kwargs
    ) as writer:
        writer.write_table(table_value)
    return sink.getvalue().to_pybytes()


def _decode_value(response: RResponse) -> Any:
    if response.value and response.value.get("kind") == "arrow_file":
        # File-based Arrow IPC: read and delete the temp file
        return _read_arrow_file(response.value.get("path", ""))
    if response.value and response.value.get("kind") == "arrow":
        data = response.value.get("data")
        if isinstance(data, str):
            # base64-encoded Arrow IPC bytes
            arrow_bytes = base64.b64decode(data)
        elif isinstance(data, list):
            # Legacy: list-of-ints format (backward compat)
            arrow_bytes = bytes(data)
        else:
            arrow_bytes = bytes(data or [])
        return bytes_to_arrow_table(arrow_bytes)
    if response.value and response.value.get("kind") == "value":
        return response.value.get("data")
    return None


def _read_arrow_file(path: str) -> Any:
    """Read an Arrow IPC file written by the R backend and delete it."""
    import os

    if not path or not os.path.isfile(path):
        raise FileNotFoundError(f"Arrow IPC temp file not found: {path}")
    try:
        import pyarrow as pa  # type: ignore

        return pa.ipc.open_stream(pa.memory_map(path, "r")).read_all()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _render_outputs(response: RResponse) -> Any:
    import os

    outputs = []
    if response.stdout:
        outputs.append(plain_text("\n".join(response.stdout)))
    if response.stderr:
        outputs.append(plain_text("\n".join(response.stderr)))
    if response.plot_error:
        outputs.append(plain_text(response.plot_error))
    if response.error:
        outputs.append(plain_text(response.error))
    if response.plot_path and os.path.isfile(response.plot_path):
        outputs.append(image(response.plot_path))

    value = _decode_value(response)
    if value is not None:
        if _as_arrow_table(value) is not None:
            outputs.append(table(value))
        if outputs:
            replace(vstack(outputs))
        return value

    if outputs:
        replace(vstack(outputs))
    return None


@mddoc
def r(
    code: str,
    *,
    inputs: Optional[dict[str, Any]] = None,
    output: bool = True,
    capture: bool = True,
    plot: bool = True,
    plot_format: str = "png",
    plot_width: int = 960,
    plot_height: int = 640,
    plot_dpi: int = 120,
) -> Any:
    """
    Execute R code in a persistent R subprocess.

    Args:
        code: R source code to execute.
        inputs: optional mapping of input names to values to expose
            in R.
        output: whether to display outputs in the UI.
        capture: capture stdout from R.
        plot: capture plots.
        plot_format: output format for plots, ``"png"`` (default) or
            ``"svg"``.
        plot_width: plot width in pixels (default 960).
        plot_height: plot height in pixels (default 640).
        plot_dpi: plot resolution in dots per inch (default 120).

    Returns:
        The resulting value, or an Arrow table if a dataframe is
        returned.
    """
    if code is None or code.strip() == "":
        return None

    session = get_session()
    encoded_inputs = _encode_inputs(inputs or {})
    response = session.execute(
        code,
        inputs=encoded_inputs,
        capture=capture,
        plot=plot,
        plot_format=plot_format,
        plot_width=plot_width,
        plot_height=plot_height,
        plot_dpi=plot_dpi,
    )
    if not response.ok:
        if output:
            _render_outputs(response)
        error_message = response.error or "R execution failed"
        raise RuntimeError(error_message)

    if output:
        return _render_outputs(response)
    return _decode_value(response)


def reset_session() -> None:
    """Reset the R session for the current kernel."""
    reset_r_session()
