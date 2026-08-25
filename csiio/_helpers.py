import logging
import os
import struct

import pandas as pd

LOGGER = logging.getLogger(__name__)

BASEDATE = pd.Timestamp(
    year=1990,
    month=1,
    day=1,
    hour=0,
    second=0,
    microsecond=0,
)

_DEFAULT_HEADER_VALUES = (
    "TOA5",
    "converted",
    "converted",
    "converted",
    "converted",
    "converted",
    "converted",
)


def _resolve_parallel_workers(task_count, max_workers=None):
    if task_count <= 0:
        return 1
    cpu_count = os.cpu_count() or 1

    if max_workers is not None:
        if not isinstance(max_workers, int):
            raise TypeError("max_workers must be an integer")
        if max_workers < 1:
            raise ValueError("max_workers must be >= 1")
        if max_workers > cpu_count:
            raise ValueError(f"max_workers must be <= available CPU count ({cpu_count})")
        return min(max_workers, task_count)

    default_workers = max(1, cpu_count // 4)
    return max(1, min(default_workers, task_count))


def _emit(message, level="info", quiet=False):
    log_func = getattr(LOGGER, level, LOGGER.info)
    log_func(message)
    if quiet and level == "info":
        return
    if not LOGGER.hasHandlers():
        print(message)


def get_csi_formats():
    return {
        "FP2": ">H",
        "IEEE4": "f",
        "IEEE4B": ">f",
        "UINT2": ">H",
        "INT4": ">i",
        "UINT4": ">L",
        "String": "s",
        "Boolean": "?",
        "Bool8": "8?",
        "Bool4": "4?",
        "LONG": "=L",
        "ULONG": "=L",
    }


def read_csi_formats(csformat):
    pyformat = []
    knownformats = get_csi_formats()
    for fmt in csformat:
        if fmt.startswith("ASCII"):
            n_string = fmt.replace(")", "").split("(")
            pyformat.append(n_string[1] + "s")
        elif fmt in knownformats:
            pyformat.append(knownformats[fmt])
        else:
            _emit(
                "Warning: The format code " + fmt + " is not known \n"
                "please adapt the known formats (a dictionary) "
                "using the correct identifier from "
                "https://docs.python.org/3/library/struct.html",
                level="warning",
            )

    return pyformat


def meta2binary_csiformats(meta, columns=None):
    if meta[0][0] not in ["TOB1", "TOB3"]:
        return None
    else:
        # pre-defined binary formats for TOB1/TOB3 but we added the TIMESTAMP directly.
        if ["ULONG", "ULONG", "ULONG"] == meta[-1][1:3]:
            if columns is None:
                return meta[-1]
        else:
            return {
                col: meta[-1][colno]
                for colno, col in enumerate(meta[1])
                if col in columns and meta[-1][colno]
            }


def _split_name_and_unit(label):
    if label.endswith(")") and "(" in label:
        name, unit = label.rsplit("(", 1)
        return name.strip(), unit.strip(") ")
    return label, ""


def _ensure_datetime_index(dataframe):
    if "TIMESTAMP" in dataframe.columns:
        return dataframe.set_index("TIMESTAMP")
    if not isinstance(dataframe.index, pd.DatetimeIndex):
        raise ValueError("DataFrame must have a datetime index or a TIMESTAMP column")
    return dataframe


def _is_csi_file(filepath):
    if not os.path.exists(filepath):
        return False
    prefix_map = {"TOA5", "TOACI1", "TOB1", "TOB3", "CSIXML"}
    try:
        with open(filepath, "rb") as fobj:
            head = fobj.read(64).decode("utf-8", errors="ignore").lstrip()
    except (OSError, UnicodeDecodeError):
        return False
    if head.startswith('"'):
        head = head.lstrip('"').lstrip()

    if head.startswith("<?xml"):
        return "<csixml" in head.lower()

    if head.startswith("<csixml"):
        return True

    return any(head.startswith(prefix) for prefix in prefix_map)


def _merge_dataframes(existing, new):
    existing = _ensure_datetime_index(existing).copy()
    new = _ensure_datetime_index(new)

    overlapping = existing.index.intersection(new.index)
    if not overlapping.empty:
        existing = existing.drop(overlapping)

    merged = pd.concat([existing, new]).sort_index()
    if "RECORD (RN)" in merged.columns:
        merged["RECORD (RN)"] = range(1, len(merged) + 1)
    return merged


def _is_csv_file(filepath):
    if not os.path.exists(filepath):
        return False
    try:
        with open(filepath, encoding="utf-8") as fobj:
            head = fobj.read(1024)
    except (OSError, UnicodeDecodeError):
        return False
    return "," in head or "\t" in head


def _prepare_output_for_existing(output_file, dataframe, exists_action, quiet=True):
    if exists_action == "overwrite":
        return dataframe

    if exists_action == "skip" and os.path.exists(output_file):
        _emit(f"Skipping existing output file {output_file}", quiet=quiet)
        return None

    if exists_action == "merge":
        if not os.path.exists(output_file):
            return dataframe

        if _is_csi_file(output_file):
            existing, _ = __import__(
                "csiio.read_csi_files", fromlist=["read_csi_files"]
            ).read_csi_files(output_file, quiet=quiet)
            return _merge_dataframes(existing, dataframe)

        if _is_csv_file(output_file):
            existing = pd.read_csv(output_file, parse_dates=["TIMESTAMP"], index_col="TIMESTAMP")
            return _merge_dataframes(existing, dataframe)

        raise ValueError(
            f"Cannot merge existing output because '{output_file}' is neither a CSI nor a CSV file"
        )

    raise ValueError("exists_action must be one of overwrite, skip, or merge")


def _resolve_split_group_freq(split_window):
    if isinstance(split_window, pd.Timedelta):
        if split_window <= pd.Timedelta(0):
            raise ValueError("split_window must be a positive duration")
        return split_window
    if isinstance(split_window, str):
        freq = pd.Timedelta(split_window.lower())
        if freq <= pd.Timedelta(0):
            raise ValueError("split_window must be a positive duration")
        return freq
    if isinstance(split_window, int):
        if split_window <= 0:
            raise ValueError("split_window must be a positive duration")
        return pd.Timedelta(f"{split_window}D")
    raise ValueError("split_window must be a pandas offset alias string or integer number of days")


def _timestamped_output_path(output_file, start_ts, end_ts):
    stem, ext = os.path.splitext(output_file)
    start_str = start_ts.strftime("%Y%m%d_%H%M%S")
    end_str = end_ts.strftime("%Y%m%d_%H%M%S")
    return f"{stem}_{start_str}_{end_str}{ext}"


def _resolve_meta_header_value(meta, row_index, col_index, current_value):
    if meta and len(meta) > row_index and len(meta[row_index]) > col_index:
        meta_value = meta[row_index][col_index]
        if meta_value not in (None, "") and current_value == _DEFAULT_HEADER_VALUES[col_index]:
            return meta_value
    return current_value


def _resolve_meta_process_values(meta, current_value, field_count):
    if current_value != "Smp":
        return [current_value] * field_count

    if meta and len(meta) > 3:
        process_values = [value if value not in (None, "") else current_value for value in meta[3]]
        if len(process_values) < field_count:
            process_values.extend([current_value] * (field_count - len(process_values)))
        return process_values[:field_count]

    return [current_value] * field_count


def _iter_split_chunks(dataframe, split_window, closed="left", label="left"):
    group_freq = _resolve_split_group_freq(split_window)
    grouped = dataframe.groupby(pd.Grouper(freq=group_freq, closed=closed, label=label))
    for _, chunk in grouped:
        if chunk.empty:
            continue
        yield chunk, chunk.index.min(), chunk.index.max()


def _prepare_export_dataframe(dataframe):
    export_df = dataframe.copy()
    export_df = _ensure_datetime_index(export_df)
    if "RECORD (RN)" not in export_df.columns:
        export_df.insert(0, "RECORD (RN)", range(1, len(export_df) + 1))
    return export_df


def fp22float(fp2integer):
    from .read_csi_files import fp22float as _fp22float

    return _fp22float(fp2integer)


def _to_float_if_possible(value):
    from .read_csi_files import _to_float_if_possible as _read_to_float_if_possible

    return _read_to_float_if_possible(value)


def _coerce_timestamp_index(values):
    from .read_csi_files import _coerce_timestamp_index as _read_coerce_timestamp_index

    return _read_coerce_timestamp_index(values)


def _data_to_dataframe(data, meta, filetype, sortindex=True):
    from .read_csi_files import _data_to_dataframe as _read_data_to_dataframe

    return _read_data_to_dataframe(data, meta, filetype, sortindex=sortindex)


def _normalize_output_path(output_file, output_format):
    outdir = os.path.dirname(output_file)
    base = os.path.basename(output_file)
    stem, _ = os.path.splitext(base)
    prefix = output_format.upper() + "_"
    if not stem.upper().startswith(prefix):
        stem = prefix + stem
    ext = ".csv" if output_format.upper() == "CSV" else ".dat"
    return os.path.join(outdir, stem + ext)


def _infer_struct_format(series, tob3=False):
    non_null = series.dropna()
    if non_null.empty:
        if pd.api.types.is_string_dtype(series) or pd.api.types.is_object_dtype(series):
            return "ASCII(1)"
        return "IEEE4B" if tob3 else "IEEE4"

    if pd.api.types.is_bool_dtype(non_null):
        return "Boolean"

    if pd.api.types.is_integer_dtype(non_null):
        minv, maxv = int(non_null.min()), int(non_null.max())
        if minv >= 0:
            if maxv <= 2**16 - 1:
                return "UINT2"
            return "UINT4"
        return "INT4"

    numeric_values = pd.to_numeric(non_null, errors="coerce")
    if numeric_values.notna().all():
        return "IEEE4B" if tob3 else "IEEE4"

    if pd.api.types.is_string_dtype(series) or pd.api.types.is_object_dtype(series):
        max_length = max(len(str(value).encode("utf-8")) for value in non_null)
        return f"ASCII({max(1, max_length)})"

    return "IEEE4B" if tob3 else "IEEE4"


def _pack_value(fmt, value):
    if pd.isna(value):
        if fmt.endswith("s"):
            n = int(fmt[:-1])
            return b"\x00" * n
        if fmt in ["?", "4?", "8?"]:
            value = False
        elif fmt[-1] in ["H", "L", "I", "l", "i"]:
            value = 0
        else:
            value = float("nan")

    if fmt.endswith("s"):
        n = int(fmt[:-1])
        txt = str(value).encode("utf-8")[:n]
        txt = txt + b"\x00" * max(0, n - len(txt))
        return struct.pack(fmt, txt)

    if fmt in ["?", "4?", "8?"]:
        return struct.pack(fmt, bool(value))

    if fmt[-1] in ["H", "L", "I", "l", "i"]:
        print(fmt, value)
        return struct.pack(fmt, int(value))

    return struct.pack(fmt, float(value))
