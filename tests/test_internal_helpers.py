import importlib
import io
from contextlib import redirect_stdout
from pathlib import Path

import pandas as pd
import pytest

rcf = importlib.import_module("csiio.read_csi_files")


def test_resolve_parallel_workers_zero_tasks_returns_one():
    assert rcf._resolve_parallel_workers(0) == 1


def test_emit_prints_when_no_handlers_and_not_quiet_info(monkeypatch):
    monkeypatch.setattr(rcf.LOGGER, "hasHandlers", lambda: False)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rcf._emit("hello")
    assert "hello" in buf.getvalue()


def test_fp22float_special_values_and_to_float_if_possible():
    assert rcf.fp22float(0x1FFF) == float("inf")
    assert rcf.fp22float(0x9FFF) == float("-inf")
    assert pd.isna(rcf.fp22float(0x9FFE))
    assert rcf._to_float_if_possible("3.5") == 3.5
    assert rcf._to_float_if_possible("x") == "x"


def test_coerce_timestamp_index_multiple_formats():
    values = [
        "2024-01-01 00:00:00.123",
        "2024-01-01 00:00:01",
        "2024-01-01T00:00:02.456",
        "2024-01-01T00:00:03",
    ]
    out = rcf._coerce_timestamp_index(values)
    assert isinstance(out, pd.DatetimeIndex)
    assert len(out) == 4
    assert out.isna().sum() == 0


def test_read_csi_formats_unknown_code_emits_warning(monkeypatch):
    seen = []
    monkeypatch.setattr(
        rcf, "_emit", lambda msg, level="info", quiet=False: seen.append((msg, level))
    )
    out = rcf.read_csi_formats(["FP2", "ASCII(4)", "MYSTERY"])
    assert out[0] == ">H"
    assert out[1] == "4s"
    assert any(level == "warning" for _, level in seen)


def test_data_to_dataframe_rejects_empty_or_mismatch():
    meta = [
        ["TOA5", "a", "b", "c", "d", "e", "f"],
        ["TIMESTAMP", "RECORD", "A"],
        ["TS", "RN", "x"],
    ]
    with pytest.raises(ValueError):
        rcf._data_to_dataframe(None, meta, "TOA5")

    with pytest.raises(ValueError):
        rcf._data_to_dataframe([["2024-01-01"]], meta, "TOA5")


def test_resolve_split_group_freq_valid_and_invalid():
    assert rcf._resolve_split_group_freq("1H") == "1h"
    with pytest.raises(ValueError):
        rcf._resolve_split_group_freq("0H")
    with pytest.raises(ValueError):
        rcf._resolve_split_group_freq(pd.Timedelta(0))


def test_resolve_meta_process_values_extend_and_fallback():
    meta = [[], [], [], ["", "Avg"]]
    vals = rcf._resolve_meta_process_values(meta, "Smp", 4)
    assert vals == ["Smp", "Avg", "Smp", "Smp"]
    assert rcf._resolve_meta_process_values(meta, "Raw", 2) == ["Raw", "Raw"]


def test_infer_struct_format_and_pack_value_branches():
    assert rcf._infer_struct_format(pd.Series([True, False])) == "Boolean"
    assert rcf._infer_struct_format(pd.Series([1, 2, 3])) == "UINT2"
    assert rcf._infer_struct_format(pd.Series([1.2, 2.3])) == "IEEE4"
    assert rcf._infer_struct_format(pd.Series(["abc", "de"])) == "ASCII(3)"
    assert rcf._infer_struct_format(pd.Series(["abc", "de"]), tob3=True) == "ASCII(3)"

    # string null fill
    packed_str = rcf._pack_value("4s", pd.NA)
    assert packed_str == b"\x00\x00\x00\x00"
    # bool/int/float null coercion paths
    assert len(rcf._pack_value("?", pd.NA)) == 1
    assert len(rcf._pack_value(">H", pd.NA)) == 2
    assert len(rcf._pack_value("f", pd.NA)) == 4


def test_convert_split_chunk_writer_branches_and_unknown_output(tmp_path, monkeypatch):
    idx = pd.date_range("2024-01-01 00:00:00", periods=4, freq="30min")
    df = pd.DataFrame({"RECORD (RN)": [1, 2, 3, 4], "x": [1.0, 2.0, 3.0, 4.0]}, index=idx)
    meta = [
        ["TOA5", "a", "b", "c", "d", "e", "f"],
        ["TIMESTAMP", "RECORD", "x"],
        ["TS", "RN", ""],
        ["", "", "Smp"],
    ]

    def fake_read(*args, **kwargs):
        return df, meta, {"fake": {"header": meta[0], "fields": []}}

    monkeypatch.setattr(rcf, "_read_csi_files_impl", fake_read)

    for fmt in ["TOA5", "TOB1", "TOB3", "CSIXML"]:
        outputs = rcf._convert_csi_file_impl(
            "in.dat",
            str(tmp_path / f"out_{fmt}.dat"),
            fmt,
            quiet=True,
            split_window="1H",
            max_workers=1,
        )
        assert isinstance(outputs, list)
        assert len(outputs) == 2
        for out in outputs:
            assert Path(out).exists()

    with pytest.raises(ValueError):
        rcf._convert_csi_file_impl(
            "in.dat",
            str(tmp_path / "bad.dat"),
            "BAD",
            quiet=True,
            split_window=None,
            max_workers=1,
        )
