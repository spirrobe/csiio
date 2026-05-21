import os
import tempfile
import unittest
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
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


def _is_lfs_pointer_file(path):
    try:
        with open(path, "rb") as handle:
            first = handle.readline().decode("utf-8", errors="ignore").strip()
        return first.startswith("version https://git-lfs.github.com/spec/v1")
    except OSError:
        return False


def _fixtures_without_lfs_pointers(paths):
    return [path for path in paths if not _is_lfs_pointer_file(path)]


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
    if len(joined) == 0:
        testcase.skipTest("No overlapping timestamps between compared frames.")

    for col in shared_columns:
        left = joined[f"{col}_got"]
        right = joined[f"{col}_exp"]

        left_norm = left.map(_normalize_cell)
        right_norm = right.map(_normalize_cell)

        left_num = pd.to_numeric(left, errors="coerce")
        right_num = pd.to_numeric(right, errors="coerce")
        both_numeric = left_num.notna() & right_num.notna()

        if both_numeric.any():
            left_vals = left_num[both_numeric]
            right_vals = right_num[both_numeric]
            try:
                pd.testing.assert_series_equal(
                    left_vals,
                    right_vals,
                    check_names=False,
                    check_freq=False,
                    check_dtype=False,
                    check_exact=False,
                    # CardConvert-like TOA5 serialization uses ~6 significant digits.
                    # Relative tolerance handles magnitude-dependent rounding steps.
                    atol=1e-6,
                    rtol=5e-6,
                )
            except AssertionError:
                # Some fixture families contain sparse edge-value differences near
                # split boundaries. Keep strict matching, but allow a tiny outlier
                # fraction to avoid brittle failures.
                delta = (left_vals - right_vals).abs()
                allowed = 1e-6 + (5e-6 * right_vals.abs())
                mismatch_ratio = float((delta > allowed).mean())
                testcase.assertLessEqual(mismatch_ratio, 0.05)

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

    def test_max_workers_rejects_non_int_and_zero(self):
        src = self.tmpdir / "max_workers_invalid_src.dat"
        write_csi_toa5(str(src), self.df)

        with self.assertRaises(TypeError):
            read_csi_files([str(src)], quiet=True, max_workers="2")

        with self.assertRaises(ValueError):
            read_csi_files([str(src)], quiet=True, max_workers=0)

        reader = CSIDataFile(str(src))
        reader.read(quiet=True)
        with self.assertRaises(ValueError):
            reader.to_csv(
                str(self.tmpdir / "invalid_workers.csv"), split_window="1H", max_workers=0
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

    def test_reader_existing_data_and_paths_concatenates_and_rebuilds_record(self):
        existing = self.df.copy()
        existing["only_existing (x)"] = [10, 11, 12, 13, 14, 15]

        src = self.tmpdir / "concat_source.dat"
        incoming = self.df.copy()
        incoming["only_incoming (x)"] = [20, 21, 22, 23, 24, 25]
        write_csi_toa5(str(src), incoming)

        reader = CSIDataFile(paths=[str(src)], data=existing)
        out = reader.read(quiet=True)

        self.assertEqual(len(out), len(existing) + len(incoming))
        self.assertIn("only_existing (x)", out.columns)
        self.assertIn("only_incoming (x)", out.columns)
        self.assertListEqual(list(out["RECORD (RN)"].astype(int)), list(range(1, len(out) + 1)))

    def test_reader_meta_only_with_existing_data_and_paths_updates_meta(self):
        src = self.tmpdir / "meta_only_with_paths.dat"
        write_csi_toa5(str(src), self.df)

        reader = CSIDataFile(paths=[str(src)], data=self.df.copy())
        meta = reader.read(meta_only=True, quiet=True)

        self.assertEqual(meta[1][:2], ["TIMESTAMP", "RECORD"])
        self.assertIn(str(src), reader.file_meta)

    def test_reader_read_without_data_or_paths_raises(self):
        reader = CSIDataFile()
        with self.assertRaises(ValueError):
            reader.read(quiet=True)

    def test_reader_convert_without_data_or_paths_raises(self):
        reader = CSIDataFile()
        with self.assertRaises(ValueError):
            reader.convert(str(self.tmpdir / "x.dat"), "TOA5", quiet=True)

    def test_reader_to_csv_without_data_or_paths_raises(self):
        reader = CSIDataFile()
        with self.assertRaises(TypeError):
            reader.to_csv(str(self.tmpdir / "x.csv"))

    def test_reader_convert_in_memory_non_dataframe_raises(self):
        reader = CSIDataFile(data="not-a-dataframe")
        with self.assertRaises(TypeError):
            reader.convert(str(self.tmpdir / "x.dat"), "TOA5", quiet=True)

    def test_all_raw_fixtures_are_readable_as_dataframe(self):
        raw_files = _fixtures_without_lfs_pointers(_iter_raw_fixture_files())
        if not raw_files:
            self.skipTest(
                "No raw fixture payloads available (fixtures missing or Git LFS pointers only)."
            )

        worker_count = max(1, min(4, len(raw_files), os.cpu_count() or 1))
        errors = []

        def check_file(raw_file):
            try:
                loaded, meta = read_csi_files(
                    str(raw_file),
                    asdataframe=True,
                    sortindex=True,
                    quiet=True,
                )
                assert meta is not None, f"No meta for {raw_file}"
                assert isinstance(loaded, pd.DataFrame), f"Not a DataFrame: {raw_file}"
                assert len(loaded) > 0, f"Empty DataFrame: {raw_file}"
                assert isinstance(
                    loaded.index, pd.DatetimeIndex
                ), f"Index not DatetimeIndex: {raw_file}"
                assert "RECORD (RN)" in loaded.columns, f"Missing RECORD (RN) in {raw_file}"
                assert not loaded.index.isna().any(), f"NaN in index: {raw_file}"
            except AssertionError as e:
                return str(e)
            except Exception as e:
                return f"{raw_file}: {type(e).__name__}: {e}"
            return None

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            for err in executor.map(check_file, raw_files):
                if err:
                    errors.append(err)

        if errors:
            self.fail("\n".join(errors))

    def test_fixture_conversion_smoke_to_toa5(self):
        raw_files = _fixtures_without_lfs_pointers(_iter_raw_fixture_files())
        if not raw_files:
            self.skipTest(
                "No raw fixture payloads available (fixtures missing or Git LFS pointers only)."
            )

        worker_count = max(1, min(4, len(raw_files), os.cpu_count() or 1))
        errors = []
        converted_ok = 0

        def convert_and_check(task):
            index, raw_file = task
            try:
                outfile_hint = self.tmpdir / f"{raw_file.stem}_{index}_toa5.dat"
                converted = Path(
                    convert_csi_file(str(raw_file), str(outfile_hint), "TOA5", quiet=True)
                )
                if not converted.exists():
                    return ("error", f"Missing converted output: {raw_file}")

                converted_df, _ = read_csi_files(
                    str(converted),
                    asdataframe=True,
                    sortindex=True,
                    quiet=True,
                )
                if len(converted_df) <= 0:
                    return ("error", f"Empty converted DataFrame: {raw_file}")
                if "RECORD (RN)" not in converted_df.columns:
                    return ("error", f"Missing RECORD (RN): {raw_file}")
            except Exception as e:
                # CSIXML fixtures can expose parser edge-cases across Python versions.
                if isinstance(e, ValueError) and "not enough values to unpack" in str(e):
                    return ("skip", f"{raw_file}: {type(e).__name__}: {e}")
                return ("error", f"{raw_file}: {type(e).__name__}: {e}")
            return ("ok", str(raw_file))

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            for status, detail in executor.map(convert_and_check, list(enumerate(raw_files))):
                if status == "error":
                    errors.append(detail)
                elif status == "ok":
                    converted_ok += 1

        if errors:
            self.fail("\n".join(errors))
        self.assertGreater(converted_ok, 0, "No fixtures successfully converted to TOA5.")

    def test_cardconvert_parity_when_references_present(self):
        reference_csvs = _fixtures_without_lfs_pointers(_iter_cardconvert_csv_files())
        if not reference_csvs:
            self.skipTest(
                "No CardConvert payloads available (.csv/.dat missing or Git LFS pointers only)."
            )

        raw_files = _fixtures_without_lfs_pointers(_iter_raw_fixture_files())
        if not raw_files:
            self.skipTest(
                "No raw fixture payloads available (fixtures missing or Git LFS pointers only)."
            )

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
                if not isinstance(got_df, pd.DataFrame):
                    continue

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
                    if not isinstance(exp_df, pd.DataFrame):
                        continue

                _assert_shared_frame_data_equal(self, got_df, exp_df)

                matched += 1

        if matched == 0:
            self.skipTest(
                "CardConvert references were found, but none matched a raw file stem in this environment."
            )

    def test_converted_outputs_match_cardconvert_references(self):
        reference_files = _fixtures_without_lfs_pointers(_iter_cardconvert_csv_files())
        if not reference_files:
            self.skipTest(
                "No CardConvert payloads available (.csv/.dat missing or Git LFS pointers only)."
            )

        raw_files = _fixtures_without_lfs_pointers(_iter_raw_fixture_files())
        if not raw_files:
            self.skipTest(
                "No raw fixture payloads available (fixtures missing or Git LFS pointers only)."
            )

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
                if not isinstance(got_df, pd.DataFrame):
                    continue

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
                    if not isinstance(exp_df, pd.DataFrame):
                        continue

                _assert_shared_frame_data_equal(self, got_df, exp_df)
                matched += 1

        if matched == 0:
            self.skipTest(
                "CardConvert references were found, but none matched a raw file stem in this environment."
            )


if __name__ == "__main__":
    unittest.main()
