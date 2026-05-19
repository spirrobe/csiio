from __future__ import annotations

import argparse
import csv
import math
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from csiio import convert_csi_file, read_csi_files

FIXTURES_DIR = Path(__file__).parent / "fixtures"
RAW_FIXTURES_DIR = FIXTURES_DIR / "raw"
CARDCONVERT_FIXTURES_DIR = FIXTURES_DIR / "cardconvert"
REPORTS_DIR = Path(__file__).parent / "reports"

ATOL = 1e-6
RTOL = 5e-6
TEXT_DELTA_RATIO_TOL = 0.02


@dataclass
class AuditRow:
    raw: str
    target: str
    reference: str
    strict_status: str
    tolerance_status: str
    reason: str
    overlap_rows: int
    shared_cols: int
    exact_ratio: float
    max_abs_diff: float


def _iter_raw_fixture_files() -> list[Path]:
    if not RAW_FIXTURES_DIR.exists():
        return []
    return sorted([p for p in RAW_FIXTURES_DIR.rglob("*") if p.is_file()])


def _iter_cardconvert_files() -> list[Path]:
    if not CARDCONVERT_FIXTURES_DIR.exists():
        return []
    out = []
    for p in CARDCONVERT_FIXTURES_DIR.rglob("*"):
        if p.is_file() and p.suffix.lower() in {".csv", ".dat"}:
            out.append(p)
    return sorted(out)


def _normalized_reference_stem(stem: str) -> str:
    prefixes = ["TOA5_", "TOB1_", "TOB3_", "CSIXML_"]
    up = stem.upper()
    for prefix in prefixes:
        if up.startswith(prefix):
            return stem[len(prefix) :]
    return stem


def _find_matching_raw_file(reference_file: Path, raw_files: Iterable[Path]) -> Path | None:
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

    return sorted(candidates, key=lambda p: len(p.stem), reverse=True)[0]


def _reference_output_format(reference_file: Path) -> str | None:
    prefix = reference_file.stem.split("_", 1)[0].upper()
    if prefix not in {"TOA5", "TOB1", "TOB3", "CSIXML"}:
        return None
    return prefix


def _normalize_cell(value: object) -> str:
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


def _load_reference_frame(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".csv":
        exp_df = pd.read_csv(path)
        if "TIMESTAMP" not in exp_df.columns:
            raise ValueError(f"Reference CSV missing TIMESTAMP column: {path}")
        exp_df["TIMESTAMP"] = pd.to_datetime(exp_df["TIMESTAMP"])
        return exp_df.set_index("TIMESTAMP").sort_index()

    exp_df, _ = read_csi_files(str(path), asdataframe=True, sortindex=True, quiet=True)
    return exp_df


def _audit_pair(
    got_df: pd.DataFrame, exp_df: pd.DataFrame
) -> tuple[str, int, int, float, float, float, str, str]:
    got_no_record = got_df.drop(columns=["RECORD (RN)"], errors="ignore")
    exp_no_record = exp_df.drop(columns=["RECORD", "RN", "RECORD (RN)"], errors="ignore")

    shared_columns = sorted(set(got_no_record.columns).intersection(exp_no_record.columns))
    if not shared_columns:
        return "no_shared_columns", 0, 0, 0.0, math.inf, 1.0, "FAIL", "FAIL"

    got_shared = got_no_record[shared_columns]
    exp_shared = exp_no_record[shared_columns]
    joined = got_shared.join(exp_shared, how="inner", lsuffix="_got", rsuffix="_exp")

    overlap_rows = len(joined)
    if overlap_rows == 0:
        return "no_overlap_rows", 0, len(shared_columns), 0.0, math.inf, 1.0, "FAIL", "FAIL"

    total_cells = overlap_rows * len(shared_columns)
    exact_match_cells = 0
    max_abs_diff = 0.0

    any_numeric_out_of_tol = False
    text_delta_ratio_max = 0.0

    for col in shared_columns:
        left = joined[f"{col}_got"]
        right = joined[f"{col}_exp"]

        left_norm = left.map(_normalize_cell)
        right_norm = right.map(_normalize_cell)

        left_num = pd.to_numeric(left, errors="coerce")
        right_num = pd.to_numeric(right, errors="coerce")
        both_numeric = left_num.notna() & right_num.notna()

        if both_numeric.any():
            abs_diff = (left_num[both_numeric] - right_num[both_numeric]).abs()
            if not abs_diff.empty:
                max_abs_diff = max(max_abs_diff, float(abs_diff.max()))

            close_mask = abs_diff <= (ATOL + RTOL * right_num[both_numeric].abs())
            exact_match_cells += int((abs_diff == 0).sum())
            if not bool(close_mask.all()):
                any_numeric_out_of_tol = True

        non_numeric = ~both_numeric
        if non_numeric.any():
            left_text = left_norm[non_numeric]
            right_text = right_norm[non_numeric]

            equal_mask = left_text == right_text
            exact_match_cells += int(equal_mask.sum())

            if not bool(equal_mask.all()):
                left_counts = Counter(left_text.tolist())
                right_counts = Counter(right_text.tolist())
                keys = set(left_counts).union(right_counts)
                delta = sum(abs(left_counts[k] - right_counts[k]) for k in keys)
                ratio = delta / max(len(left_text), 1)
                text_delta_ratio_max = max(text_delta_ratio_max, ratio)

    exact_ratio = exact_match_cells / total_cells

    strict_status = "PASS" if exact_match_cells == total_cells else "FAIL"

    tolerance_ok = (not any_numeric_out_of_tol) and (text_delta_ratio_max <= TEXT_DELTA_RATIO_TOL)
    tolerance_status = "PASS" if tolerance_ok else "FAIL"

    if strict_status == "PASS":
        reason = "exact_match"
    elif overlap_rows == 0:
        reason = "no_overlap_rows"
    elif any_numeric_out_of_tol:
        reason = "numeric_outside_tolerance"
    elif text_delta_ratio_max > TEXT_DELTA_RATIO_TOL:
        reason = "text_mismatch_exceeds_tolerance"
    elif max_abs_diff > 0:
        reason = "numeric_rounding_within_tolerance"
    else:
        reason = "text_distribution_shift_within_tolerance"

    return (
        reason,
        overlap_rows,
        len(shared_columns),
        exact_ratio,
        max_abs_diff,
        text_delta_ratio_max,
        strict_status,
        tolerance_status,
    )


def generate_reports(reports_dir: Path = REPORTS_DIR) -> tuple[Path, Path]:
    raw_files = _iter_raw_fixture_files()
    reference_files = _iter_cardconvert_files()

    if not raw_files:
        raise RuntimeError(f"No raw fixtures found under {RAW_FIXTURES_DIR}")
    if not reference_files:
        raise RuntimeError(f"No CardConvert references found under {CARDCONVERT_FIXTURES_DIR}")

    reports_dir.mkdir(parents=True, exist_ok=True)

    rows: list[AuditRow] = []
    grouped: dict[tuple[str, str], list[AuditRow]] = defaultdict(list)

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        for expected in reference_files:
            raw = _find_matching_raw_file(expected, raw_files)
            target = _reference_output_format(expected)
            if raw is None or target is None:
                continue

            out_hint = tmp_path / f"{raw.stem}_{target.lower()}.dat"
            converted = Path(convert_csi_file(str(raw), str(out_hint), target, quiet=True))

            got_df, _ = read_csi_files(str(converted), asdataframe=True, sortindex=True, quiet=True)
            exp_df = _load_reference_frame(expected)

            (
                reason,
                overlap_rows,
                shared_cols,
                exact_ratio,
                max_abs_diff,
                _text_delta_ratio,
                strict_status,
                tolerance_status,
            ) = _audit_pair(got_df, exp_df)

            row = AuditRow(
                raw=raw.name,
                target=target,
                reference=expected.name,
                strict_status=strict_status,
                tolerance_status=tolerance_status,
                reason=reason,
                overlap_rows=overlap_rows,
                shared_cols=shared_cols,
                exact_ratio=exact_ratio,
                max_abs_diff=max_abs_diff,
            )
            rows.append(row)
            grouped[(row.raw, row.target)].append(row)

    strict_out = reports_dir / "conversion_audit_dual.csv"
    dual_summary_out = reports_dir / "conversion_audit_dual_summary.csv"

    with strict_out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "raw",
                "target",
                "reference",
                "strict_status",
                "tolerance_status",
                "reason",
                "overlap_rows",
                "shared_cols",
                "exact_ratio",
                "max_abs_diff",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)

    with dual_summary_out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "raw",
                "target",
                "references",
                "strict_passed",
                "strict_failed",
                "tolerance_passed",
                "tolerance_failed",
                "avg_exact_ratio",
                "worst_exact_ratio",
                "worst_max_abs_diff",
            ],
        )
        writer.writeheader()

        for (raw, target), items in sorted(grouped.items()):
            exacts = [item.exact_ratio for item in items]
            diffs = [item.max_abs_diff for item in items]
            writer.writerow(
                {
                    "raw": raw,
                    "target": target,
                    "references": len(items),
                    "strict_passed": sum(1 for x in items if x.strict_status == "PASS"),
                    "strict_failed": sum(1 for x in items if x.strict_status == "FAIL"),
                    "tolerance_passed": sum(1 for x in items if x.tolerance_status == "PASS"),
                    "tolerance_failed": sum(1 for x in items if x.tolerance_status == "FAIL"),
                    "avg_exact_ratio": sum(exacts) / len(exacts),
                    "worst_exact_ratio": min(exacts),
                    "worst_max_abs_diff": max(diffs),
                }
            )

    return strict_out, dual_summary_out


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate conversion audit reports with both strict and tolerance status, "
            "including reason codes for mismatches."
        )
    )
    parser.add_argument(
        "--reports-dir",
        default=str(REPORTS_DIR),
        help="Output folder for audit CSV files (default: tests/reports).",
    )
    args = parser.parse_args()

    strict_out, summary_out = generate_reports(Path(args.reports_dir))
    print(strict_out)
    print(summary_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
