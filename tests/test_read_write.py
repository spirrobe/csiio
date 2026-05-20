import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path

import pandas as pd

from csiio import (
    CSIDataFile,
    convert_csi_file,
    read_csi_files,
    write_csi_toa5,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
RAW_FIXTURES_DIR = FIXTURES_DIR / "raw"
CARDCONVERT_FIXTURES_DIR = FIXTURES_DIR / "cardconvert"


def _iter_raw_fixture_files():
    if not RAW_FIXTURES_DIR.exists():
        return []
    files = [p for p in RAW_FIXTURES_DIR.rglob("*") if p.is_file()]
    return sorted(files)


def _iter_cardconvert_csv_files():
    if not CARDCONVERT_FIXTURES_DIR.exists():
        return []
    files = [
        p
        for p in CARDCONVERT_FIXTURES_DIR.rglob("*")
        if p.is_file() and p.suffix.lower() in {".csv", ".dat"}
    ]
    return sorted(files)


def _normalized_reference_stem(stem):
    prefixes = ["TOA5_", "TOB1_", "TOB3_", "CSIXML_"]
    for prefix in prefixes:
        if stem.upper().startswith(prefix):
            return stem[len(prefix) :]
    return stem


def _find_matching_raw_file(reference_file, raw_files):
    ref_stem = _normalized_reference_stem(reference_file.stem)
    candidates = []

    for raw in raw_files:
        raw_stem = raw.stem
        if ref_stem == raw_stem:
            candidates.append(raw)
            continue
        if raw_stem in ref_stem:
            candidates.append(raw)

    if not candidates:
        return None

    # Prefer the most specific stem match.
    return sorted(candidates, key=lambda p: len(p.stem), reverse=True)[0]


def _reference_output_format(reference_file):
    prefix = reference_file.stem.split("_", 1)[0].upper()
    if prefix not in {"TOA5", "TOB1", "TOB3", "CSIXML"}:
        return None
    return prefix


def _assert_shared_frame_data_equal(testcase, got_df, exp_df):
    def _normalize_cell(value):
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore").rstrip("\x00")
        if pd.isna(value):
            return ""
        text = str(value).rstrip("\x00").strip()
        if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
            text = text[1:-1].strip()
        if text.upper() in {"NAN", "NA", "NULL", "NONE"}:
            return ""
        return text

    got_no_record = got_df.drop(columns=["RECORD (RN)"], errors="ignore")
    exp_no_record = exp_df.drop(columns=["RECORD", "RN", "RECORD (RN)"], errors="ignore")

    shared_columns = sorted(set(got_no_record.columns).intersection(exp_no_record.columns))
    testcase.assertGreater(len(shared_columns), 0)

    got_shared = got_no_record[shared_columns]
    exp_shared = exp_no_record[shared_columns]

    joined = got_shared.join(exp_shared, how="inner", lsuffix="_got", rsuffix="_exp")
    testcase.assertGreater(len(joined), 0)

    for col in shared_columns:
        left = joined[f"{col}_got"]
        right = joined[f"{col}_exp"]

        left_norm = left.map(_normalize_cell)
        right_norm = right.map(_normalize_cell)

        left_num = pd.to_numeric(left, errors="coerce")
        right_num = pd.to_numeric(right, errors="coerce")
        both_numeric = left_num.notna() & right_num.notna()

        if both_numeric.any():
            pd.testing.assert_series_equal(
                left_num[both_numeric],
                right_num[both_numeric],
                check_names=False,
                check_freq=False,
                check_dtype=False,
                check_exact=False,
                # CardConvert-like TOA5 serialization uses ~6 significant digits.
                # Relative tolerance handles magnitude-dependent rounding steps.
                atol=1e-6,
                rtol=5e-6,
            )

        non_numeric = ~both_numeric
        if non_numeric.any():
            left_text = left_norm[non_numeric]
            right_text = right_norm[non_numeric]
            if not (left_text == right_text).all():
                left_counts = Counter(left_text.tolist())
                right_counts = Counter(right_text.tolist())
                if left_counts != right_counts:
                    keys = set(left_counts).union(right_counts)
                    delta = sum(abs(left_counts[k] - right_counts[k]) for k in keys)
                    # Split-window boundaries can shift a small number of text states.
                    testcase.assertLessEqual(delta / max(len(left_text), 1), 0.02)


class TestCampbellScientificIO(unittest.TestCase):
    def setUp(self):
        self.tmpdir_obj = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmpdir_obj.name)

        index = pd.date_range("2024-01-01 00:00:00", periods=6, freq="30min")
        self.df = pd.DataFrame(
            {
                "air_temp (degC)": [0.5, 0.7, 0.9, 1.1, 1.4, 1.8],
                "co2_flux (umol m-2 s-1)": [0.01, -0.02, 0.05, 0.03, -0.01, 0.0],
            },
            index=index,
        )

    def tearDown(self):
        self.tmpdir_obj.cleanup()

    def test_write_and_read_toa5(self):
        src = self.tmpdir / "sample_toa5.dat"
        write_csi_toa5(str(src), self.df)

        loaded, _ = read_csi_files(
            str(src),
            asdataframe=True,
            sortindex=True,
            quiet=True,
        )

        self.assertEqual(len(loaded), len(self.df))
        self.assertTrue(isinstance(loaded.index, pd.DatetimeIndex))
        self.assertEqual(
            list(loaded.columns), ["RECORD (RN)", "air_temp (degC)", "co2_flux (umol m-2 s-1)"]
        )

    def test_convert_to_tob3_and_read(self):
        src = self.tmpdir / "source.dat"
        write_csi_toa5(str(src), self.df)

        out_hint = self.tmpdir / "converted.dat"
        converted_path = Path(convert_csi_file(str(src), str(out_hint), "TOB3", quiet=True))

        loaded, _ = read_csi_files(
            str(converted_path),
            asdataframe=True,
            sortindex=True,
            quiet=True,
        )

        self.assertEqual(len(loaded), len(self.df))
        self.assertTrue((loaded["RECORD (RN)"] >= 1).all())

        expected = self.df.copy()
        expected.index.name = "TIMESTAMP"
        numeric = loaded[["air_temp (degC)", "co2_flux (umol m-2 s-1)"]]
        pd.testing.assert_series_equal(
            numeric["air_temp (degC)"].round(6),
            expected["air_temp (degC)"].round(6),
            check_names=False,
            check_freq=False,
        )
        pd.testing.assert_series_equal(
            numeric["co2_flux (umol m-2 s-1)"].round(6),
            expected["co2_flux (umol m-2 s-1)"].round(6),
            check_names=False,
            check_freq=False,
        )

    def test_to_csv_split_window(self):
        src = self.tmpdir / "source_for_csv.dat"
        write_csi_toa5(str(src), self.df)

        reader = CSIDataFile(str(src))
        reader.read(asdataframe=True, quiet=True)

        outputs = reader.to_csv(str(self.tmpdir / "out.csv"), split_window="1H")
        self.assertGreaterEqual(len(outputs), 2)
        for output in outputs:
            self.assertTrue(Path(output).exists())
            chunk = pd.read_csv(output)
            self.assertGreater(len(chunk), 0)

    def test_dataframe_initialization_sorts_by_timestamp(self):
        unsorted = self.df.iloc[[3, 0, 5, 1, 4, 2]].copy()
        reader = CSIDataFile(data=unsorted)

        self.assertTrue(reader.data.index.is_monotonic_increasing)
        self.assertListEqual(list(reader.data.index), sorted(unsorted.index))

    def test_convert_split_window_writes_timestamped_outputs(self):
        src = self.tmpdir / "source_for_convert.dat"
        write_csi_toa5(str(src), self.df)

        outputs = convert_csi_file(
            str(src),
            str(self.tmpdir / "converted.dat"),
            "TOA5",
            quiet=True,
            split_window="1H",
        )

        self.assertIsInstance(outputs, list)
        self.assertGreaterEqual(len(outputs), 2)

        for output in outputs:
            path = Path(output)
            self.assertTrue(path.exists())
            self.assertRegex(path.name, r"^TOA5_converted_\d{8}T\d{6}_\d{8}T\d{6}\.dat$")
            loaded, _ = read_csi_files(str(path), asdataframe=True, sortindex=True, quiet=True)
            self.assertGreater(len(loaded), 0)

    def test_convert_csi_file_list_returns_one_output_per_input(self):
        src1 = self.tmpdir / "batch_source_a.dat"
        src2 = self.tmpdir / "batch_source_b.dat"
        write_csi_toa5(str(src1), self.df)
        write_csi_toa5(str(src2), self.df)

        out_dir = self.tmpdir / "batch_out"
        outputs = convert_csi_file(
            [str(src1), str(src2)],
            str(out_dir),
            "TOB3",
            quiet=True,
        )

        self.assertEqual(len(outputs), 2)
        for output in outputs:
            path = Path(output)
            self.assertTrue(path.exists())
            self.assertTrue(path.name.startswith("TOB3_"))
            loaded, _ = read_csi_files(str(path), asdataframe=True, sortindex=True, quiet=True)
            self.assertEqual(len(loaded), len(self.df))

    def test_convert_csi_file_list_split_window_flattens_outputs(self):
        src1 = self.tmpdir / "split_source_a.dat"
        src2 = self.tmpdir / "split_source_b.dat"
        write_csi_toa5(str(src1), self.df)
        write_csi_toa5(str(src2), self.df)

        out_dir = self.tmpdir / "split_batch_out"
        outputs = convert_csi_file(
            [str(src1), str(src2)],
            str(out_dir),
            "TOA5",
            quiet=True,
            split_window="1H",
        )

        self.assertGreaterEqual(len(outputs), 4)
        for output in outputs:
            path = Path(output)
            self.assertTrue(path.exists())
            self.assertRegex(path.name, r"^TOA5_split_source_[ab]_\d{8}T\d{6}_\d{8}T\d{6}\.dat$")

    def test_read_many_files_accepts_explicit_max_workers(self):
        src1 = self.tmpdir / "read_many_a.dat"
        src2 = self.tmpdir / "read_many_b.dat"
        write_csi_toa5(str(src1), self.df)
        write_csi_toa5(str(src2), self.df)

        loaded, _meta = read_csi_files([str(src1), str(src2)], quiet=True, max_workers=1)
        self.assertEqual(len(loaded), len(self.df) * 2)

    def test_max_workers_rejects_values_above_cpu_count(self):
        src = self.tmpdir / "max_workers_src.dat"
        write_csi_toa5(str(src), self.df)
        too_high = (os.cpu_count() or 1) + 1

        with self.assertRaises(ValueError):
            read_csi_files([str(src)], quiet=True, max_workers=too_high)

        with self.assertRaises(ValueError):
            convert_csi_file(
                str(src),
                str(self.tmpdir / "out.dat"),
                "TOA5",
                quiet=True,
                split_window="1H",
                max_workers=too_high,
            )

    def test_writer_uses_meta_for_header_defaults_and_allows_overrides(self):
        custom_meta = [
            [
                "TOA5",
                "station-meta",
                "logger-meta",
                "serial-meta",
                "os-meta",
                "program-meta",
                "table-meta",
            ],
            ["TIMESTAMP", "RECORD", "air_temp"],
            ["TS", "RN", "degC"],
            ["", "", "Smp"],
        ]

        meta_file = self.tmpdir / "meta_defaults.dat"
        write_csi_toa5(str(meta_file), self.df, meta=custom_meta)

        header = meta_file.read_text(encoding="utf-8").splitlines()[0]
        self.assertIn('"station-meta"', header)
        self.assertIn('"logger-meta"', header)
        self.assertIn('"serial-meta"', header)
        self.assertIn('"os-meta"', header)
        self.assertIn('"program-meta"', header)
        self.assertIn('"table-meta"', header)

        override_file = self.tmpdir / "meta_override.dat"
        write_csi_toa5(
            str(override_file),
            self.df,
            meta=custom_meta,
            station="station-override",
        )

        override_header = override_file.read_text(encoding="utf-8").splitlines()[0]
        self.assertIn('"station-override"', override_header)
        self.assertIn('"logger-meta"', override_header)

    def test_reader_keeps_normalized_meta_and_per_file_meta(self):
        toa5_path = self.tmpdir / "source_toa5.dat"
        write_csi_toa5(str(toa5_path), self.df)

        tob3_hint = self.tmpdir / "source_tob3.dat"
        tob3_path = Path(convert_csi_file(str(toa5_path), str(tob3_hint), "TOB3", quiet=True))

        reader = CSIDataFile([str(toa5_path), str(tob3_path)])
        loaded = reader.read(quiet=True)

        self.assertEqual(len(loaded), len(self.df) * 2)
        self.assertEqual(reader.meta[1][:2], ["TIMESTAMP", "RECORD"])
        self.assertEqual(set(reader.file_meta), {str(toa5_path), str(tob3_path)})
        self.assertEqual(reader.file_meta[str(toa5_path)]["filetype"], "TOA5")
        self.assertEqual(reader.file_meta[str(tob3_path)]["filetype"], "TOB3")
        self.assertTrue(
            any(
                field["name"] == "TIMESTAMP" for field in reader.file_meta[str(toa5_path)]["fields"]
            )
        )

    def test_convert_prefers_normalized_meta_over_file_meta(self):
        reader = CSIDataFile(data=self.df.copy())
        reader.meta = [
            [
                "TOA5",
                "normalized-station",
                "normalized-logger",
                "normalized-serial",
                "normalized-os",
                "normalized-program",
                "normalized-table",
            ],
            ["TIMESTAMP", "RECORD", "air_temp", "co2_flux"],
            ["TS", "RN", "degC", "umol m-2 s-1"],
            ["", "", "Smp", "Smp"],
        ]
        reader.file_meta = {
            "fake_source.dat": {
                "filetype": "TOA5",
                "header": [
                    "TOA5",
                    "file-meta-station",
                    "file-meta-logger",
                    "file-meta-serial",
                    "file-meta-os",
                    "file-meta-program",
                    "file-meta-table",
                ],
                "fields": [
                    {"name": "TIMESTAMP", "unit": "TS", "process": "", "type": ""},
                    {"name": "RECORD", "unit": "RN", "process": "", "type": ""},
                    {"name": "air_temp", "unit": "degC", "process": "Smp", "type": ""},
                ],
            }
        }

        out_file = self.tmpdir / "normalized_meta_convert.dat"
        reader.convert(str(out_file), "TOA5", quiet=True)

        header = out_file.read_text(encoding="utf-8").splitlines()[0]
        self.assertIn('"normalized-station"', header)
        self.assertIn('"normalized-logger"', header)
        self.assertIn('"normalized-program"', header)
        self.assertNotIn('"file-meta-station"', header)

    def test_all_raw_fixtures_are_readable_as_dataframe(self):
        raw_files = _iter_raw_fixture_files()
        if not raw_files:
            self.skipTest("No raw fixtures found under tests/fixtures/raw")

        for raw_file in raw_files:
            with self.subTest(raw_file=str(raw_file)):
                loaded, meta = read_csi_files(
                    str(raw_file),
                    asdataframe=True,
                    sortindex=True,
                    quiet=True,
                )
                self.assertIsNotNone(meta)
                self.assertTrue(isinstance(loaded, pd.DataFrame))
                self.assertGreater(len(loaded), 0)
                self.assertTrue(isinstance(loaded.index, pd.DatetimeIndex))
                self.assertIn("RECORD (RN)", loaded.columns)
                self.assertFalse(loaded.index.isna().any())

    def test_fixture_conversion_smoke_to_toa5(self):
        raw_files = _iter_raw_fixture_files()
        if not raw_files:
            self.skipTest("No raw fixtures found under tests/fixtures/raw")

        for raw_file in raw_files:
            with self.subTest(raw_file=str(raw_file)):
                outfile_hint = self.tmpdir / f"{raw_file.stem}_toa5.dat"
                converted = Path(
                    convert_csi_file(str(raw_file), str(outfile_hint), "TOA5", quiet=True)
                )
                self.assertTrue(converted.exists())

                converted_df, _ = read_csi_files(
                    str(converted),
                    asdataframe=True,
                    sortindex=True,
                    quiet=True,
                )
                self.assertGreater(len(converted_df), 0)
                self.assertIn("RECORD (RN)", converted_df.columns)

    def test_cardconvert_parity_when_references_present(self):
        reference_csvs = _iter_cardconvert_csv_files()
        if not reference_csvs:
            self.skipTest(
                "No CardConvert reference files (.csv/.dat) found under tests/fixtures/cardconvert"
            )

        raw_files = _iter_raw_fixture_files()
        matched = 0

        for expected_csv in reference_csvs:
            raw_file = _find_matching_raw_file(expected_csv, raw_files)
            if raw_file is None:
                # Keep running for other files while you build fixture pairs.
                continue

            with self.subTest(pair=f"{raw_file.name} <-> {expected_csv.name}"):
                got_df, _ = read_csi_files(
                    str(raw_file),
                    asdataframe=True,
                    sortindex=True,
                    quiet=True,
                )

                if expected_csv.suffix.lower() == ".csv":
                    exp_df = pd.read_csv(expected_csv)
                    self.assertGreater(len(exp_df), 0)
                    self.assertIn("TIMESTAMP", exp_df.columns)
                    exp_df["TIMESTAMP"] = pd.to_datetime(exp_df["TIMESTAMP"])
                    exp_df = exp_df.set_index("TIMESTAMP").sort_index()
                else:
                    exp_df, _ = read_csi_files(
                        str(expected_csv),
                        asdataframe=True,
                        sortindex=True,
                        quiet=True,
                    )

                    _assert_shared_frame_data_equal(self, got_df, exp_df)

                matched += 1

        self.assertGreater(
            matched,
            0,
            "CardConvert references were found, but none matched a raw file stem.",
        )

    def test_converted_outputs_match_cardconvert_references(self):
        from collections import defaultdict

        reference_files = _iter_cardconvert_csv_files()
        if not reference_files:
            self.skipTest(
                "No CardConvert reference files (.csv/.dat) found under tests/fixtures/cardconvert"
            )

        raw_files = _iter_raw_fixture_files()

        # Group reference files by (matching raw file, output format) then sample splits.
        # This keeps first + last from each daily-split window to test edge cases
        # without testing every intermediate day (e.g., 20+ daily splits from one raw file).
        groups = defaultdict(list)
        for ref_file in reference_files:
            raw_file = _find_matching_raw_file(ref_file, raw_files)
            output_format = _reference_output_format(ref_file)
            if raw_file and output_format:
                groups[(raw_file, output_format)].append(ref_file)

        sampled_files = []
        for group_files in groups.values():
            group_files = sorted(group_files)
            if len(group_files) <= 2:
                sampled_files.extend(group_files)
            else:
                # Keep first and last from split windows (e.g., first day and last day)
                sampled_files.append(group_files[0])
                sampled_files.append(group_files[-1])

        reference_files = sorted(set(sampled_files))
        matched = 0

        for expected_file in reference_files:
            raw_file = _find_matching_raw_file(expected_file, raw_files)
            output_format = _reference_output_format(expected_file)
            if raw_file is None or output_format is None:
                continue

            with self.subTest(pair=f"{raw_file.name} -> {output_format} vs {expected_file.name}"):
                outfile_hint = self.tmpdir / f"{raw_file.stem}_{output_format.lower()}.dat"
                converted = Path(
                    convert_csi_file(str(raw_file), str(outfile_hint), output_format, quiet=True)
                )

                got_df, _ = read_csi_files(
                    str(converted),
                    asdataframe=True,
                    sortindex=True,
                    quiet=True,
                )

                if expected_file.suffix.lower() == ".csv":
                    exp_df = pd.read_csv(expected_file)
                    self.assertGreater(len(exp_df), 0)
                    self.assertIn("TIMESTAMP", exp_df.columns)
                    exp_df["TIMESTAMP"] = pd.to_datetime(exp_df["TIMESTAMP"])
                    exp_df = exp_df.set_index("TIMESTAMP").sort_index()
                else:
                    exp_df, _ = read_csi_files(
                        str(expected_file),
                        asdataframe=True,
                        sortindex=True,
                        quiet=True,
                    )

                _assert_shared_frame_data_equal(self, got_df, exp_df)
                matched += 1

        self.assertGreater(
            matched,
            0,
            "CardConvert references were found, but none matched a raw file stem.",
        )


if __name__ == "__main__":
    unittest.main()
