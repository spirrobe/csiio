import io
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from csiio import cli


def _sample_df():
    idx = pd.date_range("2024-01-01", periods=2, freq="1h")
    return pd.DataFrame({"RECORD (RN)": [1, 2], "x": [1.0, 2.0]}, index=idx)


def test_comma_or_repeatable_paths():
    out = cli._comma_or_repeatable_paths(["a.dat,b.dat", " c.dat ", "d.dat,e.dat"])
    assert out == ["a.dat", "b.dat", "c.dat", "d.dat", "e.dat"]


def test_main_read_meta_only_prints_count_for_list_result():
    reader = MagicMock()
    reader.read.return_value = [["meta-a"], ["meta-b"]]

    with patch("csiio.cli.CSIDataFile", return_value=reader):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["read", "a.dat", "b.dat", "--meta-only", "--quiet"])

    assert rc == 0
    assert "metadata files: 2" in buf.getvalue()


def test_main_read_meta_only_prints_loaded_for_single_meta_result():
    reader = MagicMock()
    reader.read.return_value = {"filetype": "TOA5"}

    with patch("csiio.cli.CSIDataFile", return_value=reader):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["read", "a.dat", "--meta-only", "--quiet"])

    assert rc == 0
    assert "metadata loaded" in buf.getvalue()


def test_main_read_as_csv_writes_csv_to_stdout():
    reader = MagicMock()
    reader.read.return_value = _sample_df()

    with patch("csiio.cli.CSIDataFile", return_value=reader):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["read", "a.dat", "--as-csv", "--quiet"])

    assert rc == 0
    output = buf.getvalue()
    assert "RECORD (RN)" in output
    assert "2024-01-01 00:00:00" in output


def test_main_read_meta_only_and_as_csv_rejected():
    with pytest.raises(ValueError):
        cli.main(["read", "a.dat", "--meta-only", "--as-csv"])


def test_main_read_rejects_non_dataframe_result():
    reader = MagicMock()
    reader.read.return_value = "not-a-dataframe"

    with patch("csiio.cli.CSIDataFile", return_value=reader):
        with pytest.raises(TypeError):
            cli.main(["read", "a.dat", "--quiet"])


def test_main_read_prints_shape_and_column_count_when_not_as_csv():
    reader = MagicMock()
    reader.read.return_value = _sample_df()

    with patch("csiio.cli.CSIDataFile", return_value=reader):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["read", "a.dat", "--quiet"])

    assert rc == 0
    output = buf.getvalue()
    assert "dataframe shape:" in output
    assert "columns:" in output


def test_main_convert_prints_multiple_outputs_and_passes_max_workers():
    with patch("csiio.cli.convert_csi_file", return_value=["o1.dat", "o2.dat"]) as convert_mock:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(
                [
                    "convert",
                    "in.dat",
                    "--output",
                    "out.dat",
                    "--output-format",
                    "TOA5",
                    "--split-window",
                    "1H",
                    "--max-workers",
                    "3",
                    "--quiet",
                ]
            )

    assert rc == 0
    assert "o1.dat" in buf.getvalue()
    assert "o2.dat" in buf.getvalue()
    kwargs = convert_mock.call_args.kwargs
    assert kwargs["max_workers"] == 3
    assert kwargs["split_window"] == "1H"


def test_main_convert_prints_single_output_path():
    with patch("csiio.cli.convert_csi_file", return_value="single.dat"):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(
                [
                    "convert",
                    "in.dat",
                    "--output",
                    "out.dat",
                    "--output-format",
                    "TOB3",
                    "--quiet",
                ]
            )

    assert rc == 0
    assert "single.dat" in buf.getvalue()


def test_main_convert_passes_exists_action_to_convert_csi_file():
    with patch("csiio.cli.convert_csi_file", return_value="out.dat") as convert_mock:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(
                [
                    "convert",
                    "in.dat",
                    "--output",
                    "out.dat",
                    "--output-format",
                    "TOA5",
                    "--exists-action",
                    "merge",
                    "--quiet",
                ]
            )

    assert rc == 0
    assert "out.dat" in buf.getvalue()
    assert convert_mock.call_args.kwargs["exists_action"] == "merge"


def test_main_to_csv_passes_max_workers_to_read_and_to_csv():
    reader = MagicMock()
    reader.to_csv.return_value = ["out_a.csv", "out_b.csv"]

    with patch("csiio.cli.CSIDataFile", return_value=reader):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(
                [
                    "to-csv",
                    "a.dat,b.dat",
                    "--output",
                    "out.csv",
                    "--split-window",
                    "1H",
                    "--max-workers",
                    "2",
                    "--quiet",
                ]
            )

    assert rc == 0
    assert "out_a.csv" in buf.getvalue()
    assert "out_b.csv" in buf.getvalue()

    read_kwargs = reader.read.call_args.kwargs
    assert read_kwargs["max_workers"] == 2

    csv_kwargs = reader.to_csv.call_args.kwargs
    assert csv_kwargs["max_workers"] == 2
    assert csv_kwargs["split_window"] == "1H"


def test_main_unknown_command_prints_help_and_returns_2():
    fake_parser = MagicMock()
    fake_parser.parse_args.return_value = type("Args", (), {"command": "unknown"})()

    with patch("csiio.cli.build_parser", return_value=fake_parser):
        rc = cli.main(["unknown"])

    assert rc == 2
    fake_parser.print_help.assert_called_once()
