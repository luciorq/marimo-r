"""Tests for marimo._r.r — encoding, decoding, and type handling.

Covers:
- _encode_inputs: Arrow table encoding (base64), scalar values
- _json_compatible: numpy, datetime, set, bytes, LazyFrame
- _as_arrow_table: pyarrow, pandas, polars, record batch
- _arrow_to_bytes: IPC serialization with optional LZ4 compression
- _decode_value: base64 Arrow decoding, legacy list-of-ints, scalars
- _write_arrow_file / _read_arrow_file: temp file transfer for large payloads
- Round-trip type fidelity
"""

from __future__ import annotations

import base64
import datetime
import os
import tempfile
from typing import Any
from unittest.mock import MagicMock

import pytest

from marimo._r.r import (
    ARROW_FILE_THRESHOLD,
    _arrow_to_bytes,
    _as_arrow_table,
    _decode_value,
    _encode_inputs,
    _json_compatible,
    _read_arrow_file,
    _write_arrow_file,
)
from marimo._r.session import RResponse

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


def _make_response(
    kind: str = "value",
    data: Any = None,
) -> RResponse:
    return RResponse(
        ok=True,
        stdout=[],
        stderr=[],
        value={"kind": kind, "data": data},
        plot_path=None,
        plot_error=None,
        error=None,
    )


# ===================================================================
# _json_compatible
# ===================================================================


class TestJsonCompatible:
    """Test the custom JSON-compatible value converter."""

    def test_none(self) -> None:
        assert _json_compatible(None) is None

    def test_bool(self) -> None:
        assert _json_compatible(True) is True
        assert _json_compatible(False) is False

    def test_int(self) -> None:
        assert _json_compatible(42) == 42

    def test_float(self) -> None:
        assert _json_compatible(3.14) == 3.14

    def test_str(self) -> None:
        assert _json_compatible("hello") == "hello"

    def test_list(self) -> None:
        assert _json_compatible([1, "a", None]) == [1, "a", None]

    def test_tuple_becomes_list(self) -> None:
        assert _json_compatible((1, 2, 3)) == [1, 2, 3]

    def test_dict(self) -> None:
        assert _json_compatible({"a": 1, "b": 2}) == {"a": 1, "b": 2}

    def test_dict_int_keys_become_strings(self) -> None:
        assert _json_compatible({1: "a", 2: "b"}) == {"1": "a", "2": "b"}

    def test_nested_structures(self) -> None:
        value = {"a": [1, {2: "x"}], "b": (True, None)}
        result = _json_compatible(value)
        assert result == {"a": [1, {"2": "x"}], "b": [True, None]}

    def test_set_becomes_sorted_list(self) -> None:
        result = _json_compatible({3, 1, 2})
        assert result == [1, 2, 3]

    def test_frozenset_becomes_sorted_list(self) -> None:
        result = _json_compatible(frozenset([3, 1, 2]))
        assert result == [1, 2, 3]

    def test_bytes_becomes_base64(self) -> None:
        result = _json_compatible(b"hello")
        assert result == base64.b64encode(b"hello").decode("ascii")

    def test_bytearray_becomes_base64(self) -> None:
        result = _json_compatible(bytearray(b"world"))
        expected = base64.b64encode(b"world").decode("ascii")
        assert result == expected

    def test_datetime(self) -> None:
        dt = datetime.datetime(2026, 3, 31, 12, 0, 0)
        assert _json_compatible(dt) == "2026-03-31T12:00:00"

    def test_date(self) -> None:
        d = datetime.date(2026, 3, 31)
        assert _json_compatible(d) == "2026-03-31"

    def test_time(self) -> None:
        t = datetime.time(12, 30, 45)
        assert _json_compatible(t) == "12:30:45"

    def test_numpy_integer(self) -> None:
        np = pytest.importorskip("numpy")
        assert _json_compatible(np.int64(42)) == 42
        assert isinstance(_json_compatible(np.int64(42)), int)

    def test_numpy_floating(self) -> None:
        np = pytest.importorskip("numpy")
        result = _json_compatible(np.float64(3.14))
        assert abs(result - 3.14) < 1e-10
        assert isinstance(result, float)

    def test_numpy_bool(self) -> None:
        np = pytest.importorskip("numpy")
        assert _json_compatible(np.bool_(True)) is True
        assert isinstance(_json_compatible(np.bool_(True)), bool)

    def test_numpy_array(self) -> None:
        np = pytest.importorskip("numpy")
        result = _json_compatible(np.array([1, 2, 3]))
        assert result == [1, 2, 3]

    def test_polars_lazyframe_raises(self) -> None:
        pl = pytest.importorskip("polars")
        lf = pl.LazyFrame({"x": [1, 2, 3]})
        with pytest.raises(TypeError, match="LazyFrame"):
            _json_compatible(lf)

    def test_unknown_type_passes_through(self) -> None:
        """Unknown types are returned as-is so json.dumps can error
        with a clear traceback."""

        class Custom:
            pass

        obj = Custom()
        assert _json_compatible(obj) is obj


# ===================================================================
# _as_arrow_table
# ===================================================================


class TestAsArrowTable:
    def test_pyarrow_table_passthrough(self) -> None:
        pa = pytest.importorskip("pyarrow")
        tbl = pa.table({"x": [1, 2, 3]})
        assert _as_arrow_table(tbl) is tbl

    def test_pyarrow_record_batch(self) -> None:
        pa = pytest.importorskip("pyarrow")
        batch = pa.record_batch({"x": [1, 2, 3]})
        result = _as_arrow_table(batch)
        assert isinstance(result, pa.Table)
        assert result.num_rows == 3

    def test_pandas_dataframe(self) -> None:
        pa = pytest.importorskip("pyarrow")
        pd = pytest.importorskip("pandas")
        df = pd.DataFrame({"x": [1, 2, 3], "y": [4.0, 5.0, 6.0]})
        result = _as_arrow_table(df)
        assert isinstance(result, pa.Table)
        assert result.num_rows == 3

    def test_polars_dataframe(self) -> None:
        pa = pytest.importorskip("pyarrow")
        pl = pytest.importorskip("polars")
        df = pl.DataFrame({"x": [1, 2, 3]})
        result = _as_arrow_table(df)
        assert isinstance(result, pa.Table)
        assert result.num_rows == 3

    def test_non_table_returns_none(self) -> None:
        assert _as_arrow_table(42) is None
        assert _as_arrow_table("hello") is None
        assert _as_arrow_table([1, 2, 3]) is None

    def test_to_arrow_failure_returns_none(self) -> None:
        """Objects with a to_arrow() that raises should return None."""
        obj = MagicMock(spec=["to_arrow"])
        obj.to_arrow.side_effect = RuntimeError("fail")
        assert _as_arrow_table(obj) is None


# ===================================================================
# _arrow_to_bytes
# ===================================================================


class TestArrowToBytes:
    def test_produces_valid_ipc_bytes(self) -> None:
        pa = pytest.importorskip("pyarrow")
        tbl = pa.table({"x": [1, 2, 3]})
        result = _arrow_to_bytes(tbl)
        assert isinstance(result, bytes)
        # Verify it can be read back
        reader = pa.ipc.open_stream(result)
        tbl2 = reader.read_all()
        assert tbl.equals(tbl2)

    def test_round_trip_with_multiple_columns(self) -> None:
        pa = pytest.importorskip("pyarrow")
        tbl = pa.table(
            {
                "ints": [1, 2, 3],
                "floats": [1.1, 2.2, 3.3],
                "strings": ["a", "b", "c"],
            }
        )
        result = _arrow_to_bytes(tbl)
        tbl2 = pa.ipc.open_stream(result).read_all()
        assert tbl.equals(tbl2)


# ===================================================================
# _encode_inputs
# ===================================================================


class TestEncodeInputs:
    def test_scalar_value(self) -> None:
        result = _encode_inputs({"x": 42})
        assert result == {"x": {"type": "value", "data": 42}}

    def test_string_value(self) -> None:
        result = _encode_inputs({"name": "hello"})
        assert result == {"name": {"type": "value", "data": "hello"}}

    def test_arrow_table_encoded_as_base64(self) -> None:
        pa = pytest.importorskip("pyarrow")
        tbl = pa.table({"x": [1, 2, 3]})
        result = _encode_inputs({"df": tbl})
        assert result["df"]["type"] == "arrow"
        # Data should be a base64 string, not a list
        assert isinstance(result["df"]["data"], str)
        # Verify it decodes to valid Arrow IPC
        decoded = base64.b64decode(result["df"]["data"])
        tbl2 = pa.ipc.open_stream(decoded).read_all()
        assert tbl.equals(tbl2)

    def test_pandas_dataframe_encoded_as_arrow(self) -> None:
        pa = pytest.importorskip("pyarrow")
        pd = pytest.importorskip("pandas")
        df = pd.DataFrame({"x": [1, 2, 3]})
        result = _encode_inputs({"df": df})
        assert result["df"]["type"] == "arrow"
        assert isinstance(result["df"]["data"], str)

    def test_polars_dataframe_encoded_as_arrow(self) -> None:
        pa = pytest.importorskip("pyarrow")
        pytest.importorskip("polars")
        import polars as pl

        df = pl.DataFrame({"x": [1, 2, 3]})
        result = _encode_inputs({"df": df})
        assert result["df"]["type"] == "arrow"
        assert isinstance(result["df"]["data"], str)

    def test_mixed_inputs(self) -> None:
        pa = pytest.importorskip("pyarrow")
        tbl = pa.table({"col": [10, 20]})
        result = _encode_inputs(
            {
                "table": tbl,
                "scalar": 42,
                "name": "test",
            }
        )
        assert result["table"]["type"] == "arrow"
        assert result["scalar"] == {"type": "value", "data": 42}
        assert result["name"] == {"type": "value", "data": "test"}

    def test_set_input_serialized(self) -> None:
        result = _encode_inputs({"tags": {3, 1, 2}})
        assert result["tags"]["type"] == "value"
        assert result["tags"]["data"] == [1, 2, 3]

    def test_datetime_input_serialized(self) -> None:
        dt = datetime.datetime(2026, 1, 15, 10, 30)
        result = _encode_inputs({"ts": dt})
        assert result["ts"]["type"] == "value"
        assert result["ts"]["data"] == "2026-01-15T10:30:00"

    def test_bytes_input_serialized(self) -> None:
        result = _encode_inputs({"raw": b"abc"})
        assert result["raw"]["type"] == "value"
        expected = base64.b64encode(b"abc").decode("ascii")
        assert result["raw"]["data"] == expected

    def test_numpy_array_input_serialized(self) -> None:
        np = pytest.importorskip("numpy")
        result = _encode_inputs({"arr": np.array([1, 2, 3])})
        assert result["arr"]["type"] == "value"
        assert result["arr"]["data"] == [1, 2, 3]

    def test_empty_inputs(self) -> None:
        result = _encode_inputs({})
        assert result == {}


# ===================================================================
# _decode_value
# ===================================================================


class TestDecodeValue:
    def test_base64_arrow_decoded(self) -> None:
        pa = pytest.importorskip("pyarrow")
        tbl = pa.table({"x": [1, 2, 3]})
        # Serialize to base64
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, tbl.schema) as w:
            w.write_table(tbl)
        b64 = base64.b64encode(sink.getvalue().to_pybytes()).decode("ascii")
        resp = _make_response(kind="arrow", data=b64)
        result = _decode_value(resp)
        assert isinstance(result, pa.Table)
        assert result.equals(tbl)

    def test_legacy_list_of_ints_decoded(self) -> None:
        """Backward compatibility with the old list-of-ints format."""
        pa = pytest.importorskip("pyarrow")
        tbl = pa.table({"x": [1, 2, 3]})
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, tbl.schema) as w:
            w.write_table(tbl)
        int_list = list(sink.getvalue().to_pybytes())
        resp = _make_response(kind="arrow", data=int_list)
        result = _decode_value(resp)
        assert isinstance(result, pa.Table)
        assert result.equals(tbl)

    def test_scalar_value_decoded(self) -> None:
        resp = _make_response(kind="value", data=42)
        assert _decode_value(resp) == 42

    def test_string_value_decoded(self) -> None:
        resp = _make_response(kind="value", data="hello")
        assert _decode_value(resp) == "hello"

    def test_null_value_returns_none(self) -> None:
        resp = _make_response(kind="value", data=None)
        assert _decode_value(resp) is None

    def test_no_value_returns_none(self) -> None:
        resp = RResponse(
            ok=True,
            stdout=[],
            stderr=[],
            value=None,
            plot_path=None,
            plot_error=None,
            error=None,
        )
        assert _decode_value(resp) is None

    def test_list_value_decoded(self) -> None:
        resp = _make_response(kind="value", data=[1, 2, 3])
        assert _decode_value(resp) == [1, 2, 3]

    def test_dict_value_decoded(self) -> None:
        resp = _make_response(kind="value", data={"a": 1})
        assert _decode_value(resp) == {"a": 1}


# ===================================================================
# Round-trip: _encode_inputs -> _decode_value
# ===================================================================


class TestEncodeDecodeRoundTrip:
    """Verify that encoding then decoding preserves type fidelity
    for Arrow table values."""

    def test_arrow_table_round_trip(self) -> None:
        pa = pytest.importorskip("pyarrow")
        tbl = pa.table(
            {
                "ints": [1, 2, 3],
                "floats": [1.1, 2.2, 3.3],
                "strings": ["a", "b", "c"],
            }
        )
        encoded = _encode_inputs({"df": tbl})
        # Simulate what the R backend would return
        resp = _make_response(
            kind="arrow",
            data=encoded["df"]["data"],
        )
        result = _decode_value(resp)
        assert isinstance(result, pa.Table)
        # Column names match
        assert result.column_names == tbl.column_names
        assert result.num_rows == tbl.num_rows

    def test_pandas_round_trip(self) -> None:
        pa = pytest.importorskip("pyarrow")
        pd = pytest.importorskip("pandas")
        df = pd.DataFrame(
            {
                "x": [10, 20, 30],
                "y": ["a", "b", "c"],
            }
        )
        encoded = _encode_inputs({"df": df})
        resp = _make_response(
            kind="arrow",
            data=encoded["df"]["data"],
        )
        result = _decode_value(resp)
        assert isinstance(result, pa.Table)
        assert result.num_rows == 3
        assert set(result.column_names) == {"x", "y"}

    def test_polars_round_trip(self) -> None:
        pa = pytest.importorskip("pyarrow")
        pl = pytest.importorskip("polars")
        df = pl.DataFrame({"a": [1, 2], "b": [3.0, 4.0]})
        encoded = _encode_inputs({"df": df})
        resp = _make_response(
            kind="arrow",
            data=encoded["df"]["data"],
        )
        result = _decode_value(resp)
        assert isinstance(result, pa.Table)
        assert result.num_rows == 2


# ===================================================================
# interop: arrow_to_duckdb_relation, arrow_to_polars
# ===================================================================


class TestInteropConversions:
    def test_arrow_to_polars(self) -> None:
        pa = pytest.importorskip("pyarrow")
        pl = pytest.importorskip("polars")
        from marimo._r.interop import arrow_to_polars

        tbl = pa.table({"x": [1, 2, 3], "y": [4.0, 5.0, 6.0]})
        result = arrow_to_polars(tbl)
        assert isinstance(result, pl.DataFrame)
        assert result.shape == (3, 2)
        assert result["x"].to_list() == [1, 2, 3]

    def test_arrow_to_duckdb_relation(self) -> None:
        pa = pytest.importorskip("pyarrow")
        duckdb = pytest.importorskip("duckdb")
        from marimo._r.interop import arrow_to_duckdb_relation

        tbl = pa.table({"x": [1, 2, 3]})
        conn = duckdb.connect()
        rel = arrow_to_duckdb_relation(tbl, connection=conn)
        result = rel.fetchall()
        assert result == [(1,), (2,), (3,)]
        conn.close()

    def test_bytes_to_arrow_table(self) -> None:
        pa = pytest.importorskip("pyarrow")
        from marimo._r.interop import bytes_to_arrow_table

        tbl = pa.table({"x": [1, 2, 3]})
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, tbl.schema) as w:
            w.write_table(tbl)
        payload = sink.getvalue().to_pybytes()
        result = bytes_to_arrow_table(payload)
        assert isinstance(result, pa.Table)
        assert result.equals(tbl)


# ===================================================================
# _write_arrow_file / _read_arrow_file
# ===================================================================


class TestWriteArrowFile:
    """Test temp file writing for large Arrow payloads."""

    def test_writes_file_and_returns_descriptor(self) -> None:
        data = b"fake arrow ipc data"
        result = _write_arrow_file(data)
        assert result["type"] == "arrow_file"
        assert os.path.isfile(result["path"])
        with open(result["path"], "rb") as f:
            assert f.read() == data
        os.unlink(result["path"])

    def test_file_has_ipc_extension(self) -> None:
        data = b"test"
        result = _write_arrow_file(data)
        assert result["path"].endswith(".ipc")
        os.unlink(result["path"])

    def test_file_has_marimo_prefix(self) -> None:
        data = b"test"
        result = _write_arrow_file(data)
        basename = os.path.basename(result["path"])
        assert basename.startswith("marimo-arrow-")
        os.unlink(result["path"])


class TestReadArrowFile:
    """Test reading Arrow IPC from temp files."""

    def test_reads_and_deletes_file(self) -> None:
        pa = pytest.importorskip("pyarrow")
        tbl = pa.table({"x": [1, 2, 3]})
        arrow_bytes = _arrow_to_bytes(tbl)
        fd, path = tempfile.mkstemp(suffix=".ipc")
        with open(fd, "wb") as f:
            f.write(arrow_bytes)
        assert os.path.isfile(path)
        result = _read_arrow_file(path)
        assert isinstance(result, pa.Table)
        assert result.equals(tbl)
        # File should be deleted after reading
        assert not os.path.isfile(path)

    def test_missing_file_raises(self) -> None:
        with pytest.raises(FileNotFoundError, match="not found"):
            _read_arrow_file("/tmp/nonexistent-file-12345.ipc")

    def test_empty_path_raises(self) -> None:
        with pytest.raises(FileNotFoundError, match="not found"):
            _read_arrow_file("")


class TestEncodeInputsFileTransfer:
    """Test that _encode_inputs uses file transfer for large payloads."""

    def test_small_table_uses_base64(self) -> None:
        pa = pytest.importorskip("pyarrow")
        tbl = pa.table({"x": [1, 2, 3]})
        result = _encode_inputs({"df": tbl})
        assert result["df"]["type"] == "arrow"
        assert isinstance(result["df"]["data"], str)

    def test_large_table_uses_file(self) -> None:
        pa = pytest.importorskip("pyarrow")
        # Create a table large enough to exceed the threshold
        n = ARROW_FILE_THRESHOLD // 4 + 1000
        tbl = pa.table({"x": list(range(n))})
        result = _encode_inputs({"df": tbl})
        assert result["df"]["type"] == "arrow_file"
        assert os.path.isfile(result["df"]["path"])
        # Clean up
        os.unlink(result["df"]["path"])

    def test_large_table_file_contains_valid_ipc(self) -> None:
        pa = pytest.importorskip("pyarrow")
        n = ARROW_FILE_THRESHOLD // 4 + 1000
        tbl = pa.table({"x": list(range(n))})
        result = _encode_inputs({"df": tbl})
        path = result["df"]["path"]
        tbl2 = pa.ipc.open_stream(pa.memory_map(path, "r")).read_all()
        assert tbl.equals(tbl2)
        os.unlink(path)

    def test_mixed_large_and_small(self) -> None:
        pa = pytest.importorskip("pyarrow")
        small = pa.table({"x": [1, 2, 3]})
        n = ARROW_FILE_THRESHOLD // 4 + 1000
        large = pa.table({"x": list(range(n))})
        result = _encode_inputs(
            {
                "small": small,
                "large": large,
                "scalar": 42,
            }
        )
        assert result["small"]["type"] == "arrow"
        assert result["large"]["type"] == "arrow_file"
        assert result["scalar"]["type"] == "value"
        os.unlink(result["large"]["path"])


class TestDecodeValueFileTransfer:
    """Test that _decode_value handles arrow_file kind."""

    def test_arrow_file_decoded(self) -> None:
        pa = pytest.importorskip("pyarrow")
        tbl = pa.table({"x": [1, 2, 3]})
        arrow_bytes = _arrow_to_bytes(tbl)
        fd, path = tempfile.mkstemp(suffix=".ipc")
        with open(fd, "wb") as f:
            f.write(arrow_bytes)
        resp = _make_response(kind="arrow_file", data=None)
        # Inject the path
        resp.value["path"] = path
        result = _decode_value(resp)
        assert isinstance(result, pa.Table)
        assert result.equals(tbl)
        assert not os.path.isfile(path)

    def test_arrow_file_missing_raises(self) -> None:
        resp = _make_response(kind="arrow_file", data=None)
        resp.value["path"] = "/tmp/nonexistent-12345.ipc"
        with pytest.raises(FileNotFoundError):
            _decode_value(resp)


class TestFileTransferRoundTrip:
    """End-to-end: encode via file, decode via file."""

    def test_large_table_round_trip(self) -> None:
        pa = pytest.importorskip("pyarrow")
        n = ARROW_FILE_THRESHOLD // 4 + 1000
        tbl = pa.table(
            {
                "ints": list(range(n)),
                "strs": [f"row_{i}" for i in range(n)],
            }
        )
        encoded = _encode_inputs({"df": tbl})
        assert encoded["df"]["type"] == "arrow_file"
        # Simulate the R side returning the same kind
        resp = _make_response(kind="arrow_file", data=None)
        # Use a fresh file (as if R wrote it)
        arrow_bytes = _arrow_to_bytes(tbl)
        fd, path = tempfile.mkstemp(suffix=".ipc")
        with open(fd, "wb") as f:
            f.write(arrow_bytes)
        resp.value["path"] = path
        result = _decode_value(resp)
        assert isinstance(result, pa.Table)
        assert result.column_names == tbl.column_names
        assert result.num_rows == tbl.num_rows
        # Clean up the encode-side file
        os.unlink(encoded["df"]["path"])

    def test_threshold_boundary_uses_base64(self) -> None:
        """Tables just below threshold should use base64."""
        pa = pytest.importorskip("pyarrow")
        # Create a small table well under threshold
        tbl = pa.table({"x": [1, 2, 3]})
        encoded = _encode_inputs({"df": tbl})
        assert encoded["df"]["type"] == "arrow"
        assert isinstance(encoded["df"]["data"], str)


class TestSessionToArrowTableFileTransfer:
    """Test RSession.to_arrow_table with arrow_file kind."""

    def test_arrow_file_kind(self) -> None:
        pa = pytest.importorskip("pyarrow")
        from marimo._r.session import RSession

        tbl = pa.table({"x": [1, 2, 3]})
        arrow_bytes = _arrow_to_bytes(tbl)
        fd, path = tempfile.mkstemp(suffix=".ipc")
        with open(fd, "wb") as f:
            f.write(arrow_bytes)

        session = RSession()
        result = session.to_arrow_table(
            {
                "kind": "arrow_file",
                "path": path,
            }
        )
        assert isinstance(result, pa.Table)
        assert result.equals(tbl)
        assert not os.path.isfile(path)

    def test_arrow_file_missing_raises(self) -> None:
        from marimo._r.session import RSession

        session = RSession()
        with pytest.raises(ValueError, match="not found"):
            session.to_arrow_table(
                {
                    "kind": "arrow_file",
                    "path": "/tmp/nonexistent-12345.ipc",
                }
            )


# ===================================================================
# DuckDB relation support
# ===================================================================


class TestDuckDBRelationAsInput:
    """Test that DuckDB relations are converted to Arrow for R input."""

    def test_duckdb_relation_encoded_as_arrow(self) -> None:
        pa = pytest.importorskip("pyarrow")
        duckdb = pytest.importorskip("duckdb")
        rel = duckdb.sql("SELECT 1 AS x, 2 AS y")
        result = _encode_inputs({"rel": rel})
        assert result["rel"]["type"] == "arrow"
        assert isinstance(result["rel"]["data"], str)
        # Verify it decodes to valid Arrow IPC
        decoded = base64.b64decode(result["rel"]["data"])
        tbl = pa.ipc.open_stream(decoded).read_all()
        assert tbl.num_rows == 1
        assert set(tbl.column_names) == {"x", "y"}

    def test_duckdb_relation_with_many_rows(self) -> None:
        pa = pytest.importorskip("pyarrow")
        duckdb = pytest.importorskip("duckdb")
        rel = duckdb.sql("SELECT i FROM range(100) t(i)")
        result = _encode_inputs({"rel": rel})
        assert result["rel"]["type"] == "arrow"
        decoded = base64.b64decode(result["rel"]["data"])
        tbl = pa.ipc.open_stream(decoded).read_all()
        assert tbl.num_rows == 100

    def test_as_arrow_table_handles_duckdb_relation(self) -> None:
        pa = pytest.importorskip("pyarrow")
        duckdb = pytest.importorskip("duckdb")
        rel = duckdb.sql("SELECT 42 AS val")
        result = _as_arrow_table(rel)
        assert isinstance(result, pa.Table)
        assert result.num_rows == 1
        assert result.column("val").to_pylist() == [42]

    def test_json_compatible_rejects_duckdb_relation(self) -> None:
        duckdb = pytest.importorskip("duckdb")
        rel = duckdb.sql("SELECT 1")
        with pytest.raises(TypeError, match="DuckDB relation"):
            _json_compatible(rel)


class TestDuckDBRelationToArrowInterop:
    """Test the duckdb_relation_to_arrow helper."""

    def test_basic_conversion(self) -> None:
        pa = pytest.importorskip("pyarrow")
        duckdb = pytest.importorskip("duckdb")
        from marimo._r.interop import duckdb_relation_to_arrow

        rel = duckdb.sql("SELECT 1 AS x, 'hello' AS y")
        result = duckdb_relation_to_arrow(rel)
        assert isinstance(result, pa.Table)
        assert result.num_rows == 1
        assert result.column("x").to_pylist() == [1]
        assert result.column("y").to_pylist() == ["hello"]

    def test_multi_row_conversion(self) -> None:
        pa = pytest.importorskip("pyarrow")
        duckdb = pytest.importorskip("duckdb")
        from marimo._r.interop import duckdb_relation_to_arrow

        rel = duckdb.sql("SELECT i, i * 2 AS doubled FROM range(1000) t(i)")
        result = duckdb_relation_to_arrow(rel)
        assert isinstance(result, pa.Table)
        assert result.num_rows == 1000
        assert set(result.column_names) == {"i", "doubled"}
