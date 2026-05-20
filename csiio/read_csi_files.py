import datetime
import logging
import os
import struct
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from typing import Any

import pandas as pd

__author__ = "spirro00"

LOGGER = logging.getLogger(__name__)

BASEDATE = datetime.datetime(
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


def _emit(message, level="info", quiet=False):
    log_func = getattr(LOGGER, level, LOGGER.info)
    log_func(message)
    if quiet and level == "info":
        return
    if not LOGGER.hasHandlers():
        print(message)


def fp22float(fp2integer):
    inf, neginf, nan = 0x1FFF, 0x9FFF, 0x9FFE

    if fp2integer == inf:
        return float("inf")
    if fp2integer == neginf:
        return -float("inf")
    if fp2integer == nan:
        return float("NaN")

    mantissa, exponent = fp2integer & 0x1FFF, (fp2integer & 0x6000) >> 13
    floatvalue = mantissa * 10 ** (-1.0 * exponent)
    if fp2integer & 0x8000:
        floatvalue *= -1
    return floatvalue


def _to_float_if_possible(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _coerce_timestamp_index(values):
    """Convert mixed CSI timestamp strings to DatetimeIndex using pandas.

    CSI files can mix second-only and fractional-second timestamps in the same
    column. Parse in passes to avoid NaT rows from strict single-format parsing.
    """
    parsed = pd.to_datetime(values, format="%Y-%m-%d %H:%M:%S.%f", errors="coerce")

    if isinstance(parsed, pd.DatetimeIndex):
        parsed = pd.Series(parsed)

    missing = parsed.isna()
    if missing.any():
        parsed.loc[missing] = pd.to_datetime(
            pd.Series(values)[missing], format="%Y-%m-%d %H:%M:%S", errors="coerce"
        )

    missing = parsed.isna()
    if missing.any():
        parsed.loc[missing] = pd.to_datetime(
            pd.Series(values)[missing], format="%Y-%m-%dT%H:%M:%S.%f", errors="coerce"
        )

    missing = parsed.isna()
    if missing.any():
        parsed.loc[missing] = pd.to_datetime(
            pd.Series(values)[missing], format="%Y-%m-%dT%H:%M:%S", errors="coerce"
        )

    missing = parsed.isna()
    if missing.any():
        parsed.loc[missing] = pd.to_datetime(pd.Series(values)[missing], errors="coerce")

    return pd.DatetimeIndex(parsed)


def read_csi_formats(csformat):
    pyformat = []
    knownformats = {
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
        "LONG": "l",
        "ULONG": "=L",
    }
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


def _data_to_dataframe(data, meta, filetype, sortindex=True):
    # TOB3 keeps field names one row lower than TOA5/TOACI1 in metadata.
    name_row = 2 if filetype == "TOB3" else 1
    unit_row = name_row + 1

    record_col = meta[name_row].index("RECORD")
    if len(meta) > unit_row:
        cols = [i + f" ({j})" for i, j in zip(meta[name_row], meta[unit_row], strict=False)]
    else:
        cols = list(meta[name_row])

    if data is None:
        raise ValueError("No data were read from file before DataFrame conversion.")
    if len(data) < len(cols):
        raise ValueError(
            f"Metadata/data column mismatch: expected {len(cols)} columns, got {len(data)}"
        )

    dataframe = pd.DataFrame({col: values for col, values in zip(cols, data, strict=False)})
    dataframe = dataframe.set_index(dataframe.columns[0])
    dataframe.index.name = "TIMESTAMP"
    if sortindex:
        dataframe = dataframe.sort_index()

    dataframe = dataframe[dataframe.columns[record_col - 1 :]]

    record_col_name = [c for c in dataframe.columns if c == "RECORD" or c.startswith("RECORD (")][0]
    dataframe[record_col_name] = pd.to_numeric(dataframe[record_col_name], errors="coerce").astype(
        "Int64"
    )
    return dataframe


def _meta_rows_to_dict(meta_rows):
    filetype = meta_rows[0][0] if meta_rows and meta_rows[0] else "unknown"
    name_row = 2 if filetype == "TOB3" else 1
    unit_row = name_row + 1
    process_row = unit_row + 1
    type_row = process_row + 1

    names = list(meta_rows[name_row]) if len(meta_rows) > name_row else []
    units = list(meta_rows[unit_row]) if len(meta_rows) > unit_row else []
    process = list(meta_rows[process_row]) if len(meta_rows) > process_row else []
    types = list(meta_rows[type_row]) if len(meta_rows) > type_row else []

    fields = []
    for idx, name in enumerate(names):
        fields.append(
            {
                "name": name,
                "unit": units[idx] if idx < len(units) else "",
                "process": process[idx] if idx < len(process) else "",
                "type": types[idx] if idx < len(types) else "",
            }
        )

    return {
        "filetype": filetype,
        "header": list(meta_rows[0]) if meta_rows else [],
        "fields": fields,
    }


def _normalized_meta_from_file_meta(file_meta):
    if not file_meta:
        return [
            ["TOA5", "unknown", "unknown", "unknown", "unknown", "unknown", "unknown"],
            ["TIMESTAMP", "RECORD"],
            ["TS", "RN"],
            ["", ""],
        ]

    ordered_fields = []
    for meta_dict in file_meta.values():
        ordered_fields.extend(meta_dict.get("fields", []))

    merged_fields = {}
    for field in ordered_fields:
        merged_fields.setdefault(field.get("name", ""), field)

    first_meta = next(iter(file_meta.values()))

    names = ["TIMESTAMP", "RECORD"]
    units = [
        merged_fields.get("TIMESTAMP", {}).get("unit", "TS"),
        merged_fields.get("RECORD", {}).get("unit", "RN"),
    ]
    process = [
        merged_fields.get("TIMESTAMP", {}).get("process", ""),
        merged_fields.get("RECORD", {}).get("process", ""),
    ]

    for field in ordered_fields:
        name = field.get("name", "")
        if not name or name in {"TIMESTAMP", "RECORD"}:
            continue
        if name in names:
            continue
        names.append(name)
        units.append(field.get("unit", ""))
        process.append(field.get("process", ""))

    header = list(
        first_meta.get(
            "header", ["TOA5", "unknown", "unknown", "unknown", "unknown", "unknown", "unknown"]
        )
    )
    return [
        header,
        names,
        units,
        process,
    ]


def _read_csi_files_impl(
    filename, meta_only=False, quiet=True, sortindex=True, collect_file_meta=False, **kwargs
):
    # Backward compatibility: ignore legacy non-DataFrame flags.
    requested_as_dataframe = kwargs.pop("asdataframe", True)
    if requested_as_dataframe is False and not quiet:
        _emit(
            "Ignoring asdataframe=False: read path is DataFrame-only.", level="warning", quiet=quiet
        )

    if isinstance(filename, list):
        results = [
            _read_csi_files_impl(
                file,
                meta_only=meta_only,
                quiet=quiet,
                sortindex=sortindex,
                collect_file_meta=collect_file_meta,
                **kwargs,
            )
            for file in filename
        ]
        if meta_only:
            if collect_file_meta:
                metas, file_meta = zip(*results, strict=False)
                merged_file_meta = {}
                for item in file_meta:
                    merged_file_meta.update(item)
                return list(metas), merged_file_meta
            return results

        if collect_file_meta:
            dataframes, meta, file_meta = zip(*results, strict=False)
            merged_file_meta = {}
            for item in file_meta:
                merged_file_meta.update(item)
            dataframe = pd.concat(dataframes)
            if sortindex:
                dataframe = dataframe.sort_index()
            return dataframe, list(meta), merged_file_meta

        dataframes, meta = zip(*results, strict=False)
        dataframe = pd.concat(dataframes)
        if sortindex:
            dataframe = dataframe.sort_index()
        return dataframe, list(meta)

    filename = filename.strip()

    with open(filename, mode="rb") as file_obj:
        data = None
        firstline = file_obj.readline().rstrip().decode().split(sep=",")
        firstline = [i.replace('"', "") for i in firstline]
        filetype = firstline[0]
        if "<?xml" in firstline[0]:
            # we have an xml file, and the campbell scientific xml version is given on line 2
            # shorthand is csixml
            firstline = file_obj.readline().rstrip().decode().split(sep=",")
            firstline = [i.replace('"', "") for i in firstline]
            csixml = firstline[0][1:-1].split(" ")
            if csixml[0] != "csixml":
                if not quiet:
                    _emit(
                        "Filecontent indicated XML but apparently it's not a csixml file",
                        quiet=quiet,
                    )
                return False, False
            else:
                csixmlversion = float(csixml[1].split("=")[-1].strip("\"'"))
                if csixmlversion > 1.0:
                    _emit(
                        "This reader has been written for CSIXML version 1.0, but the version is "
                        + str(csixmlversion),
                        level="warning",
                    )
                filetype = csixml[0].upper()
        else:
            file_obj.seek(0)

        if not quiet:
            _emit("reading header and determination of filetype", quiet=quiet)

        meta = read_csi_meta(file_obj, filetype)
        raw_meta = [list(row) for row in meta]
        per_file_meta = {filename: _meta_rows_to_dict(raw_meta)}

        if meta_only:
            if collect_file_meta:
                return meta, per_file_meta
            return meta

        if not quiet:
            _emit("Reading the file " + filename, quiet=quiet)

        if filetype in ["TOA5", "TOB1", "TOACI1", "TOB3", "CSIXML"]:
            if not quiet:
                _emit(filename + " is a " + filetype + "-File", quiet=quiet)
            if filetype in ["TOA5", "TOACI1"]:
                data = read_csi_toa5(file_obj, meta, **kwargs)

            elif filetype == "TOB1":
                data = read_csi_tob1(file_obj, meta, **kwargs)
                for i in range(3):
                    meta[i] = [meta[i][1]] + meta[i][2:]

            elif filetype == "TOB3":
                data = read_csi_tob3(file_obj, meta, quiet=quiet, **kwargs)
                # Update meta to reflect TIMESTAMP and RECORD columns added by reader
                meta[2].insert(0, "RECORD")
                meta[2].insert(0, "TIMESTAMP")
                meta[3].insert(0, "RN")
                meta[3].insert(0, "TS")
                meta[4].insert(0, " ")
                meta[4].insert(0, " ")
                meta[5].insert(0, "ULONG")
                meta[5].insert(0, "DATETIME")

            elif filetype == "CSIXML":
                data = read_csi_csixml(file_obj, meta, **kwargs)
            if collect_file_meta:
                return data, meta, per_file_meta
            return data, meta

        else:
            if not quiet:
                _emit("Neither TOA5,TOB1, TOB3 not CSIXML-File", quiet=quiet)
            return False, False


@dataclass
class CSIDataFile:
    """Stateful reader/writer wrapper around Campbell Scientific data files.

    Usage:
        one = CSIDataFile('/path/to/file.dat')
        df = one.read()
        one.to_csv('/tmp/out.csv')

        many = CSIDataFile(['/path/a.dat', '/path/b.dat'])
        df_all = many.read()
        many.to_csv('/tmp/out.csv', split_window='1H')

        # Initialize from DataFrame:
        df = pd.DataFrame({'value': [1, 2, 3]},
                          index=pd.date_range('2020-01-01', periods=3))
        csdf = CSIDataFile(data=df)
        csdf.to_csv('/tmp/out.csv')
    """

    paths: Any = None
    data: Any = None
    meta: Any = None
    file_meta: Any = None

    def __post_init__(self):
        if self.file_meta is None:
            self.file_meta = {}

        # Handle data parameter (DataFrame initialization)
        if isinstance(self.data, pd.DataFrame):
            self.data = self._normalize_dataframe(self.data)
            self.meta = self._meta_from_dataframe(self.data)
            self.file_meta = {}
            # Paths are optional when data is provided
            if self.paths is None:
                self.paths = []
            elif isinstance(self.paths, list | tuple):
                self.paths = [str(p).strip() for p in self.paths]
            else:
                self.paths = [str(self.paths).strip()]
            return

        # Handle paths parameter (file-based initialization)
        if self.paths is None:
            self.paths = []
            return
        if isinstance(self.paths, list | tuple):
            self.paths = [str(p).strip() for p in self.paths]
        else:
            self.paths = [str(self.paths).strip()]

    def _normalize_dataframe(self, df):
        """Ensure DataFrame has TIMESTAMP index and RECORD column (auto-generated if missing)."""
        result = df.copy()

        # Ensure datetime index
        if not isinstance(result.index, pd.DatetimeIndex):
            result.index = pd.to_datetime(result.index)
        result.index.name = "TIMESTAMP"
        result = result.sort_index()

        # Ensure RECORD column exists
        if "RECORD (RN)" not in result.columns:
            result.insert(0, "RECORD (RN)", range(1, len(result) + 1))

        return result

    def _meta_from_dataframe(self, df):
        """Build a TOA5-compatible meta structure from a normalized DataFrame."""
        names = ["TIMESTAMP", "RECORD"] + [
            _split_name_and_unit(c)[0] for c in df.columns if c != "RECORD (RN)"
        ]
        units = ["TS", "RN"] + [
            _split_name_and_unit(c)[1] for c in df.columns if c != "RECORD (RN)"
        ]
        process = ["", ""] + ["Smp"] * (len(names) - 2)
        return [
            ["TOA5", "unknown", "unknown", "unknown", "unknown", "unknown", "unknown"],
            names,
            units,
            process,
        ]

    def _concatenate_dataframes(self, existing, new):
        """Concatenate two DataFrames with column alignment."""
        if existing is None:
            return new

        # Align columns: add missing columns with NaN
        all_cols = set(existing.columns) | set(new.columns)
        for col in all_cols:
            if col not in existing.columns:
                existing[col] = pd.NA
            if col not in new.columns:
                new[col] = pd.NA

        # Re-generate RECORD column after concatenation
        combined = pd.concat([existing, new]).sort_index()
        combined["RECORD (RN)"] = range(1, len(combined) + 1)

        return combined

    def read(self, meta_only=False, quiet=True, sortindex=True, **kwargs):
        # If data already exists and no paths provided, return stored data
        if not self.paths and self.data is not None:
            return self.data

        # If data already exists and paths are provided, load and concatenate
        if self.data is not None and self.paths:
            input_path = self.paths if len(self.paths) > 1 else self.paths[0]
            result = _read_csi_files_impl(
                input_path,
                meta_only=meta_only,
                quiet=quiet,
                sortindex=sortindex,
                collect_file_meta=True,
                **kwargs,
            )
            if meta_only:
                normalized_meta, new_file_meta = result
                self.file_meta.update(new_file_meta)
                self.meta = _normalized_meta_from_file_meta(self.file_meta)
                return self.meta

            new_data, _new_meta, new_file_meta = result
            self.file_meta.update(new_file_meta)

            self.data = self._concatenate_dataframes(self.data, new_data)
            self.meta = _normalized_meta_from_file_meta(self.file_meta)
            return self.data

        # If no data exists yet, load from files
        if not self.paths:
            raise ValueError(
                "No input path or data configured. Set CSIDataFile.paths, pass data, or set CSIDataFile.data."
            )

        input_path = self.paths if len(self.paths) > 1 else self.paths[0]

        result = _read_csi_files_impl(
            input_path,
            meta_only=meta_only,
            quiet=quiet,
            sortindex=sortindex,
            collect_file_meta=True,
            **kwargs,
        )

        if meta_only:
            _raw_meta, file_meta = result
            self.file_meta = file_meta
            self.meta = _normalized_meta_from_file_meta(self.file_meta)
            return self.meta

        data, _raw_meta, file_meta = result
        self.file_meta = file_meta
        self.data = data
        self.meta = _normalized_meta_from_file_meta(self.file_meta)

        return self.data

    def convert(self, output_file, output_format, quiet=True):
        writer_meta = self.meta

        # Support conversion from in-memory DataFrame
        if self.data is not None and not self.paths:
            if not isinstance(self.data, pd.DataFrame):
                raise TypeError("CSIDataFile.convert requires DataFrame data. Call read().")
            data = self.data
            if output_format.upper() in ["TOA5", "TOACI1"]:
                write_csi_toa5(output_file, data, filetype=output_format.upper(), meta=writer_meta)
            elif output_format.upper() == "TOB1":
                write_csi_tob1(output_file, data, meta=writer_meta)
            elif output_format.upper() == "TOB3":
                write_csi_tob3(output_file, data, meta=writer_meta)
            elif output_format.upper() == "CSIXML":
                write_csi_csixml(output_file, data, meta=writer_meta)
            else:
                raise ValueError(f"Unknown output format: {output_format}")
            return output_file

        if not self.paths:
            raise ValueError(
                "No input path or data configured. Set CSIDataFile.paths or pass data during initialization."
            )

        if len(self.paths) == 1:
            return _convert_csi_file_impl(self.paths[0], output_file, output_format, quiet=quiet)
        return convert_csi_file(self.paths, output_file, output_format, quiet=quiet)

    def to_csv(self, output_file, split_window=None, index_label="TIMESTAMP", **kwargs):
        if self.data is None:
            # Try to load from paths if available
            if self.paths:
                self.read()
            else:
                raise TypeError(
                    "No data loaded for CSV export. Set CSIDataFile.data or call read()."
                )

        if self.data is None:
            raise TypeError("No data loaded for CSV export.")

        dataframe = self.data.copy() if isinstance(self.data, pd.DataFrame) else self.data
        if not isinstance(dataframe, pd.DataFrame):
            raise TypeError("CSIDataFile.to_csv requires DataFrame data. Call read().")

        dataframe = _ensure_datetime_index(dataframe).sort_index()

        if split_window is None:
            dataframe.to_csv(output_file, index_label=index_label, **kwargs)
            return [output_file]

        os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
        outputs = []
        for chunk, start_ts, end_ts in _iter_split_chunks(dataframe, split_window):
            outfile = _timestamped_output_path(output_file, start_ts, end_ts)
            chunk.to_csv(outfile, index_label=index_label, **kwargs)
            outputs.append(outfile)

        return outputs


def read_csi_files(filename, meta_only=False, quiet=True, sortindex=True, **kwargs):
    return _read_csi_files_impl(
        filename, meta_only=meta_only, quiet=quiet, sortindex=sortindex, **kwargs
    )


def read_csi_meta(file_obj, filetype):
    filetypes = {
        "TOA5": 4,
        "TOB1": 5,
        "TOB3": 6,
        "TOACI1": 2,
        "CSIXML": -1,  # CSIXML has a variable number of headerlines
    }
    metalines = filetypes.get(filetype, 0)
    if metalines >= 0:
        meta = [file_obj.readline().rstrip().decode().split(",") for _ in range(metalines)]
    else:  # CSIXML
        tree = ElementTree.parse(file_obj.name)
        root = tree.getroot()
        head = list(root)[0]

        fields_node = None
        field_defs_node = None
        for child in list(head):
            tag = child.tag.lower()
            if tag.endswith("field_defs"):
                field_defs_node = child
            elif tag.endswith("fields"):
                fields_node = child

        # Prefer field_defs when present (contains normalized name/type/unit attrs).
        source_node = field_defs_node if field_defs_node is not None else fields_node
        field_nodes = (
            []
            if source_node is None
            else [node for node in list(source_node) if node.tag.lower().endswith("field")]
        )

        names = []
        units = []
        process = []
        types = []

        for idx, node in enumerate(field_nodes, start=1):
            name = node.attrib.get("name") or (node.text or "").strip() or f"V{idx}"
            unit = node.attrib.get("unit", node.attrib.get("units", ""))
            names.append(name)
            units.append(unit)
            process.append(node.attrib.get("process", ""))
            types.append(node.attrib.get("type", ""))

        upper_names = [n.upper() for n in names]
        has_timestamp = "TIMESTAMP" in upper_names
        has_record = "RECORD" in upper_names

        if not has_record:
            names.insert(0, "RECORD")
            units.insert(0, "RN")
            process.insert(0, "")
            types.insert(0, "ULONG")

        if not has_timestamp:
            names.insert(0, "TIMESTAMP")
            units.insert(0, "TS")
            process.insert(0, "")
            types.insert(0, "DATETIME")

        meta = [
            ["CSIXML"],
            names,
            units,
            process,
            types,
        ]
    for i, row in enumerate(meta):
        meta[i] = [j.replace('"', "") for j in row]
    return meta


def read_csi_convert_tob3_daterec(seconds):
    return pd.to_timedelta(seconds, unit="s") + BASEDATE


def read_csi_convert_tob1_daterec(daterec, units):
    timedeltas = [
        pd.to_timedelta(daterec[unit_no], unit=unit) for unit_no, unit in enumerate(units)
    ]
    datevec = timedeltas[0] + BASEDATE
    for td in timedeltas[1:]:
        datevec += td
    return datevec


def read_csi_csixml(file_obj, meta, guesstype=False):
    tree = ElementTree.parse(file_obj.name)
    root = tree.getroot()
    root_children = list(root)
    # we will need a nested list for the data
    # [1] contains the data
    data = []
    for record in root_children[1]:
        entry = [
            record.attrib["time"],
            record.attrib["no"],
        ]
        payload = [rec.text for rec in record]
        # Some writer variants include RECORD both as attribute and first payload value.
        if payload and payload[0] == record.attrib.get("no"):
            payload = payload[1:]
        entry += payload
        data.append(entry)

    # Transpose to column-oriented (always bycol internally)
    data = list(map(list, zip(*data, strict=False)))

    if guesstype:
        for i in range(len(data[1:])):
            data[i + 1] = [_to_float_if_possible(j) for j in data[i + 1]]

    # Build DataFrame from column-oriented data
    names = meta[1]
    units = meta[2] if len(meta) > 2 else [""] * len(names)
    cols = [i + f" ({j})" if j else i for i, j in zip(names, units, strict=False)]

    dataframe = pd.DataFrame({col: values for col, values in zip(cols, data, strict=False)})
    dataframe = dataframe.set_index(dataframe.columns[0])
    dataframe.index = _coerce_timestamp_index(dataframe.index)
    dataframe.index.name = "TIMESTAMP"
    dataframe = dataframe.sort_index()

    # Ensure RECORD column is Int64
    record_col_name = [
        c for c in dataframe.columns if c.upper() == "RECORD" or c.upper().startswith("RECORD (")
    ]
    if record_col_name:
        dataframe[record_col_name[0]] = pd.to_numeric(
            dataframe[record_col_name[0]], errors="coerce"
        ).astype("Int64")

    return dataframe


def read_csi_toa5(file_obj, meta, guesstype=False):
    data = [i.rstrip().decode().replace('"', "").split(sep=",") for i in file_obj]

    # Transpose to column-oriented (always bycol internally)
    data = list(map(list, zip(*data, strict=False)))

    if guesstype:
        for i in range(len(data[1:])):
            data[i + 1] = [_to_float_if_possible(j) for j in data[i + 1]]

    # Build DataFrame from column-oriented data
    names = meta[1]
    units = meta[2] if len(meta) > 2 else [""] * len(names)
    cols = [i + f" ({j})" if j else i for i, j in zip(names, units, strict=False)]

    dataframe = pd.DataFrame({col: values for col, values in zip(cols, data, strict=False)})
    dataframe = dataframe.set_index(dataframe.columns[0])
    dataframe.index = _coerce_timestamp_index(dataframe.index)
    dataframe.index.name = "TIMESTAMP"
    dataframe = dataframe.sort_index()

    # Find and ensure RECORD column is Int64
    record_col_name = [
        c for c in dataframe.columns if c.upper() == "RECORD" or c.upper().startswith("RECORD (")
    ]
    if record_col_name:
        dataframe[record_col_name[0]] = pd.to_numeric(
            dataframe[record_col_name[0]], errors="coerce"
        ).astype("Int64")

    return dataframe


def read_csi_tob1(file_obj, meta):
    csformat = meta[-1]
    pyformat = read_csi_formats(csformat)
    subrecsizes = sum(struct.Struct(fmt).size for fmt in pyformat)
    recbegin = file_obj.tell()
    n_rec_total = (os.path.getsize(file_obj.name) - recbegin) / subrecsizes
    data = []
    for _i in range(int(n_rec_total)):
        tempdata = []
        for ii in pyformat:
            nbyte = struct.Struct(ii).size
            if ii == "L":
                ii = ">L"
            tdata = struct.unpack_from(ii, file_obj.read(nbyte))[0]
            if ii == ">H":
                tdata = fp22float(tdata)
            if ii.endswith("s") and isinstance(tdata, bytes):
                tdata = tdata.decode("ascii", errors="ignore").rstrip("\x00").rstrip("\r")
            tempdata.append(tdata)
        data.append(list(tempdata))

    # Transpose to column-oriented
    data = list(map(list, zip(*data, strict=False)))
    datevec = read_csi_convert_tob1_daterec(data[:2], meta[1][:2])
    data = [datevec] + data[2:]

    # Build DataFrame from column-oriented data
    names = ["TIMESTAMP"] + meta[1][2:]  # Skip SECONDS, NANOSECONDS
    units = ["TS"] + meta[2][2:] if len(meta) > 2 else ["TS"] + [""] * len(meta[1][2:])
    cols = [i + f" ({j})" if j else i for i, j in zip(names, units, strict=False)]

    dataframe = pd.DataFrame({col: values for col, values in zip(cols, data, strict=False)})
    dataframe = dataframe.set_index(dataframe.columns[0])
    dataframe.index.name = "TIMESTAMP"
    dataframe = dataframe.sort_index()

    # Ensure RECORD column is Int64
    record_col_name = [
        c for c in dataframe.columns if c.upper() == "RECORD" or c.upper().startswith("RECORD (")
    ]
    if record_col_name:
        dataframe[record_col_name[0]] = pd.to_numeric(
            dataframe[record_col_name[0]], errors="coerce"
        ).astype("Int64")

    return dataframe


def read_csi_tob3(
    file_obj,
    meta,
    quiet=True,
):
    csformat = meta[-1]
    pyformat = read_csi_formats(csformat)
    # account for system (since the hdr is of longs of size)
    fhdrformats = ["L", "l", "i", "I"]
    hdrformat = "L"
    for _ in fhdrformats:
        if struct.Struct(3 * _).size == 12:
            hdrformat = _
    fhdr, ffoot = 3 * hdrformat, "HH"

    fhdrsize, ffootsize = struct.Struct(fhdr).size, struct.Struct(ffoot).size
    # the variables are taken from "Campbell Scientific Data File Formats"
    # by Jon Trauntvein, Thursday 13 February, 2002 Version 1.1.1.10
    framesize = meta[1][2]  # size in bytes including frameheader and framefooter

    ######## IMPORTANT FRAME VALIDATION #######
    # validation stamp, IMPORTANT
    validation = [int(meta[1][4])]
    # extend validation stamp, IMPORTANT
    validation.append(2**16 - 1 - validation[0])

    frametimeresolution = meta[1][5]
    # since only the whole frame has a timestamp, this is the delta time for subrecs
    frameresolution = int(meta[1][1].split(sep=" ")[0])
    multiplier = meta[1][1].split(sep=" ")[1]

    # should be expanded for the corrsponding amount of seconds in the mulitpliert
    time_abbr_dict = {"MIN": 60.0, "SEC": 1.0}
    multiplier_scale_dict = {"U": 10**6, "M": 10**3}
    # len > 3 gives us a scaling factor for the rest of the string
    if multiplier[0].isalpha():
        if len(multiplier) > 3:
            if multiplier[0] in multiplier_scale_dict:
                prescale = multiplier_scale_dict[multiplier[0]]
                multiplier = multiplier[1:]
            else:
                _emit(
                    f"warning, length indicates a multiplier_scale ({multiplier[0]}), but none found",
                    level="warning",
                )
                prescale = 1.0
        else:
            if not quiet:
                _emit("No multiplier_scale found", quiet=quiet)
                _emit("Abbreviation is only 3 letters long", quiet=quiet)
            prescale = 1.0

        if multiplier in time_abbr_dict:
            multiplier = prescale / time_abbr_dict[multiplier]
        else:
            multiplier = prescale / time_abbr_dict["SEC"]
            _emit("warning, time abbreviation could not be found", level="warning")
            _emit("Defaulting to seconds", level="warning")
    else:
        _emit("warning, multiplier may not be correctly parsed and is set to 1", level="warning")
        multiplier = 1

    subrec_step = frameresolution / multiplier
    scale = frametimeresolution[3:].rstrip("sec")

    nscale = int(scale[:-1])
    if scale[-1].isalpha():
        if scale[-1] == "U":
            scalefac = 10**6
        elif scale[-1] == "M":
            scalefac = 10**3
        else:
            scalefac = 1
    else:
        scalefac = 1
    subrec_scale = nscale / scalefac

    subrecsizes = sum(struct.Struct(fmt).size for fmt in pyformat)

    n_rec_frame = (int(framesize) - struct.Struct(fhdr + ffoot).size) // subrecsizes
    filesize = os.path.getsize(file_obj.name)
    last_frame_start = filesize - fhdrsize - subrecsizes * n_rec_frame - ffootsize

    seconds, recordnumber = [], []
    rec, rechdr = [], []

    while file_obj.tell() <= last_frame_start:
        binary_fhdr = file_obj.read(fhdrsize)

        if not binary_fhdr or len(binary_fhdr) < fhdrsize:
            # end of file reached
            if not quiet:
                _emit(
                    f"TOB3: unexpected EOF reading frame header {fhdrsize} {len(binary_fhdr)}",
                    quiet=quiet,
                )
            break

        rechdr.append(struct.unpack_from(fhdr, binary_fhdr))
        inpos = file_obj.tell()
        outpos = file_obj.seek(inpos + subrecsizes * n_rec_frame)
        binary_footer = file_obj.read(ffootsize)

        if not binary_footer or len(binary_footer) < ffootsize:
            # end of file reached (file_obj.read returns an emptry string)
            break

        x = struct.unpack_from(ffoot, binary_footer)

        if x[1] in validation:
            file_obj.seek(inpos)
            if x[0] != 0:
                # this is a minor frame

                temprec = []
                for ii in range(n_rec_frame):
                    minrec = []

                    for iii in pyformat:
                        recsize = struct.Struct(iii).size
                        one_record = struct.unpack_from(iii, file_obj.read(recsize))[0]

                        if iii == ">H":
                            one_record = fp22float(one_record)
                        if iii[-1] == "s" and isinstance(one_record, bytes):
                            one_record = (
                                one_record.decode("ascii", errors="ignore")
                                .rstrip("\x00")
                                .rstrip("\r")
                            )
                        minrec.append(one_record)

                    y = struct.unpack_from(ffoot, file_obj.read(ffootsize))

                    if y[1] in validation:
                        if y[0] == 0:
                            minor_rec = 0
                        else:
                            offset = bin(y[0])
                            sizeoffset = offset[6:]  # 4+2 for the 0b
                            minor_rec = int(sizeoffset, 2) - ffootsize - fhdrsize
                        n_minor_rec = minor_rec // subrecsizes
                        # compare to ii+1 because the n_minor_rec is the full number
                        # whereas the ii is from the range and starts at 0
                        if n_minor_rec == (ii + 1):
                            rec.extend(temprec)
                            recordnumber.extend(range(rechdr[-1][2], rechdr[-1][2] + n_minor_rec))
                            seconds.extend(
                                rechdr[-1][0] + (i * subrec_step + subrec_scale * rechdr[-1][1])
                                for i in range(n_minor_rec)
                            )
                            file_obj.seek(outpos + ffootsize)
                            # this breaks the for loop
                            break
                    else:
                        temprec.append(minrec)
                        # +1 on ii because at the end of this loopiteration
                        # the ii is not yet increased but we read the record
                        # and need to move on further
                        file_obj.seek(inpos + (ii + 1) * subrecsizes)
            else:
                # this is a major frame, easy
                for _ii in range(n_rec_frame):
                    temprec = []
                    for iii in pyformat:
                        one_record = struct.unpack_from(
                            iii, file_obj.read(struct.Struct(iii).size)
                        )[0]
                        if iii[-1] == "s" and isinstance(one_record, bytes):
                            one_record = (
                                one_record.decode("ascii", errors="ignore")
                                .rstrip("\x00")
                                .rstrip("\r")
                            )
                        if iii == ">H":
                            one_record = fp22float(one_record)
                        temprec.append(one_record)

                    rec.append(temprec)
                recordnumber.extend(range(rechdr[-1][2], rechdr[-1][2] + n_rec_frame))
                seconds.extend(
                    rechdr[-1][0] + (i * subrec_step + subrec_scale * rechdr[-1][1])
                    for i in range(n_rec_frame)
                )
                file_obj.seek(outpos + ffootsize)
    timestamp = [read_csi_convert_tob3_daterec(s) for s in seconds]

    if not rec:
        return pd.DataFrame()

    # Ensure all lists have same length (trim to minimum to handle off-by-one errors)
    min_len = min(len(rec), len(recordnumber), len(timestamp))
    rec = rec[:min_len]
    recordnumber = recordnumber[:min_len]
    timestamp = timestamp[:min_len]

    # Build DataFrame from records
    names = meta[2]  # TOB3 has field names in meta[2]
    units = meta[3] if len(meta) > 3 else [""] * len(names)
    cols = [i + f" ({j})" if j else i for i, j in zip(names, units, strict=False)]

    rec_df = pd.DataFrame(rec, columns=cols)
    rec_df.insert(0, "RECORD (RN)", recordnumber)
    rec_df.insert(0, "TIMESTAMP", timestamp)
    rec_df = rec_df.set_index("TIMESTAMP")
    rec_df.index.name = "TIMESTAMP"
    rec_df = rec_df.sort_index()

    # Ensure RECORD column is Int64
    rec_df["RECORD (RN)"] = pd.to_numeric(rec_df["RECORD (RN)"], errors="coerce").astype("Int64")

    return rec_df


def _split_name_and_unit(label):
    if isinstance(label, str) and label.endswith(")") and " (" in label:
        name, unit = label.rsplit(" (", 1)
        return name, unit[:-1]
    return str(label), ""


def _ensure_datetime_index(dataframe):
    if not isinstance(dataframe.index, pd.DatetimeIndex):
        dataframe.index = pd.to_datetime(dataframe.index)
    return dataframe


def _resolve_split_group_freq(split_window):
    group_freq = split_window
    if isinstance(split_window, str):
        normalized_window = split_window.strip().lower()
        window_delta = pd.to_timedelta(normalized_window)
        if window_delta <= pd.Timedelta(0):
            raise ValueError("split_window must be > 0")
        return normalized_window

    if isinstance(split_window, pd.Timedelta):
        if split_window <= pd.Timedelta(0):
            raise ValueError("split_window must be > 0")

    return group_freq


def _timestamped_output_path(output_file, start_ts, end_ts):
    stem, ext = os.path.splitext(output_file)
    ext = ext or ".dat"
    start_str = pd.Timestamp(start_ts).strftime("%Y%m%dT%H%M%S")
    end_str = pd.Timestamp(end_ts).strftime("%Y%m%dT%H%M%S")
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


def _iter_split_chunks(dataframe, split_window):
    group_freq = _resolve_split_group_freq(split_window)
    grouped = dataframe.groupby(pd.Grouper(freq=group_freq))
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


def _normalize_output_path(output_file, output_format):
    outdir = os.path.dirname(output_file)
    base = os.path.basename(output_file)
    stem, _ = os.path.splitext(base)
    prefix = output_format.upper() + "_"
    if not stem.upper().startswith(prefix):
        stem = prefix + stem
    return os.path.join(outdir, stem + ".dat")


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
        return struct.pack(fmt, int(value))

    return struct.pack(fmt, float(value))


def write_csi_toa5(
    outfile,
    dataframe,
    filetype="TOA5",
    station="converted",
    logger="converted",
    serial="converted",
    osversion="converted",
    program="converted",
    table="converted",
    meta=None,
):
    if filetype not in ["TOA5", "TOACI1"]:
        raise ValueError("filetype must be TOA5 or TOACI1")

    export_df = _prepare_export_dataframe(dataframe)

    station = _resolve_meta_header_value(meta, 0, 1, station)
    logger = _resolve_meta_header_value(meta, 0, 2, logger)
    serial = _resolve_meta_header_value(meta, 0, 3, serial)
    osversion = _resolve_meta_header_value(meta, 0, 4, osversion)
    program = _resolve_meta_header_value(meta, 0, 5, program)
    table = _resolve_meta_header_value(meta, 0, 6, table)

    raw_names = ["TIMESTAMP"] + list(export_df.columns)
    names, units = zip(*[_split_name_and_unit(col) for col in raw_names], strict=False)

    names_line = ",".join(f'"{name}"' for name in names)
    units_line = ",".join(f'"{unit}"' for unit in units)
    sampled_as_line = ",".join('""' for _ in names)

    header = [
        f'"{filetype}","{station}","{logger}","{serial}","{osversion}","{program}","{table}"',
        names_line,
        units_line,
    ]

    if filetype == "TOA5":
        header.append(sampled_as_line)

    with open(outfile, "w", encoding="utf-8") as fobj:
        fobj.write("\n".join(header) + "\n")

    write_df = export_df.copy()
    write_df.index = write_df.index.strftime("%Y-%m-%d %H:%M:%S.%f").str.rstrip("0").str.rstrip(".")
    write_df.to_csv(outfile, mode="a", header=False, index=True, float_format="%.6g")


def write_csi_csixml(outfile, dataframe, process="Smp", meta=None):
    from xml.sax.saxutils import escape

    def _xml_safe_text(value):
        if pd.isna(value):
            return ""
        text = str(value).replace("\x00", "")
        # Keep XML content single-line and remove disallowed control chars.
        text = text.replace("\r", "").replace("\n", "")
        text = "".join(ch for ch in text if ch == "\t" or ord(ch) >= 0x20)
        return text

    def _xml_field_type(series):
        numeric = pd.to_numeric(series.dropna(), errors="coerce")
        return "xsd:float" if numeric.notna().all() else "xsd:string"

    export_df = _prepare_export_dataframe(dataframe)
    payload_cols = [c for c in export_df.columns if c != "RECORD (RN)"]
    split_names = [_split_name_and_unit(col) for col in payload_cols]
    process_values = _resolve_meta_process_values(meta, process, len(payload_cols))

    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<csixml version="1.0">',
        "  <head>",
        "    <fields>",
    ]

    for (name, unit), source_col, field_process in zip(
        split_names, payload_cols, process_values, strict=False
    ):
        dtype = _xml_field_type(export_df[source_col])
        lines.append(
            f'      <field name="{escape(str(name))}" process="{escape(str(field_process))}" type="{dtype}" units="{escape(str(unit))}" />'
        )

    lines.extend(
        [
            "    </fields>",
            "  </head>",
            "  <data>",
        ]
    )

    for timestamp, row in export_df.iterrows():
        recno = int(row["RECORD (RN)"]) if "RECORD (RN)" in row else 0
        ts = timestamp.strftime("%Y-%m-%d %H:%M:%S.%f").rstrip("0").rstrip(".")
        lines.append(f'    <r time="{ts}" no="{recno}">')
        for idx, col in enumerate(payload_cols, start=1):
            value = row[col]
            text = escape(_xml_safe_text(value))
            lines.append(f"      <v{idx}>{text}</v{idx}>")
        lines.append("    </r>")

    lines.extend(
        [
            "  </data>",
            "</csixml>",
        ]
    )

    with open(outfile, "w", encoding="utf-8") as fobj:
        fobj.write("\n".join(lines) + "\n")


def write_csi_tob1(
    outfile,
    dataframe,
    station="converted",
    logger="CR1000X",
    serial="0",
    osversion="CR1000X.Std",
    program="converted",
    table="table",
    meta=None,
):
    export_df = _prepare_export_dataframe(dataframe).sort_index()

    station = _resolve_meta_header_value(meta, 0, 1, station)
    logger = _resolve_meta_header_value(meta, 0, 2, logger)
    serial = _resolve_meta_header_value(meta, 0, 3, serial)
    osversion = _resolve_meta_header_value(meta, 0, 4, osversion)
    program = _resolve_meta_header_value(meta, 0, 5, program)
    table = _resolve_meta_header_value(meta, 0, 6, table)

    payload_cols = [c for c in export_df.columns if c != "RECORD (RN)"]
    payload_names_units = [_split_name_and_unit(c) for c in payload_cols]
    payload_names = [name for name, _ in payload_names_units]
    payload_units = [unit for _, unit in payload_names_units]

    payload_formats = [_infer_struct_format(export_df[c], tob3=False) for c in payload_cols]
    pyformats = read_csi_formats(["ULONG", "ULONG", "ULONG"] + payload_formats)

    header = [
        f'"TOB1","{station}","{logger}","{serial}","{osversion}","CPU:{program}","0","{table}"',
        ",".join(f'"{x}"' for x in (["SECONDS", "NANOSECONDS", "RECORD"] + payload_names)),
        ",".join(f'"{x}"' for x in (["SECONDS", "NANOSECONDS", "RN"] + payload_units)),
        ",".join('""' for _ in ["SECONDS", "NANOSECONDS", "RECORD"])
        + ("," if payload_names else "")
        + ",".join('"Smp"' for _ in payload_names),
        ",".join(f'"{x}"' for x in (["ULONG", "ULONG", "ULONG"] + payload_formats)),
    ]

    with open(outfile, "wb") as fobj:
        fobj.write(("\n".join(header) + "\n").encode("utf-8"))

        basedate_ts = pd.Timestamp(BASEDATE)
        for ts, row in export_df.iterrows():
            delta = ts - basedate_ts
            total_ns = int(delta.total_seconds() * 1_000_000_000)
            seconds = total_ns // 1_000_000_000
            nanoseconds = total_ns % 1_000_000_000
            record = int(row["RECORD (RN)"])

            values = [seconds, nanoseconds, record] + [row[c] for c in payload_cols]
            for fmt, value in zip(pyformats, values, strict=False):
                fobj.write(_pack_value(fmt, value))


def write_csi_tob3(
    outfile,
    dataframe,
    station="converted",
    logger="CR3000",
    serial="0",
    osversion="CR3000.Std",
    program="converted",
    table="table",
    meta=None,
):
    export_df = _prepare_export_dataframe(dataframe).sort_index()

    station = _resolve_meta_header_value(meta, 0, 1, station)
    logger = _resolve_meta_header_value(meta, 0, 2, logger)
    serial = _resolve_meta_header_value(meta, 0, 3, serial)
    osversion = _resolve_meta_header_value(meta, 0, 4, osversion)
    program = _resolve_meta_header_value(meta, 0, 5, program)
    table = _resolve_meta_header_value(meta, 0, 6, table)

    payload_cols = [c for c in export_df.columns if c != "RECORD (RN)"]
    payload_names_units = [_split_name_and_unit(c) for c in payload_cols]
    payload_names = [name for name, _ in payload_names_units]
    payload_units = [unit for _, unit in payload_names_units]

    payload_formats = [_infer_struct_format(export_df[c], tob3=True) for c in payload_cols]
    pyformats = read_csi_formats(payload_formats)

    fhdrformats = ["L", "l", "i", "I"]
    hdrformat = "L"
    for _ in fhdrformats:
        if struct.Struct(3 * _).size == 12:
            hdrformat = _
    fhdr, ffoot = 3 * hdrformat, "HH"

    subrecsizes = sum(struct.Struct(fmt).size for fmt in pyformats)
    n_rec_frame = 1
    framesize = struct.Struct(fhdr + ffoot).size + subrecsizes * n_rec_frame
    validation = 60288

    header = [
        f'"TOB3","{station}","{logger}","{serial}","{osversion}","CPU:{program}","0","{table}"',
        f'"{table}","1 SEC","{framesize}","{len(export_df)}","{validation}","Sec1Usec","0","0","0"',
        ",".join(f'"{x}"' for x in payload_names),
        ",".join(f'"{x}"' for x in payload_units),
        ",".join('"Smp"' for _ in payload_names),
        ",".join(f'"{x}"' for x in payload_formats),
    ]

    with open(outfile, "wb") as fobj:
        fobj.write(("\n".join(header) + "\n").encode("utf-8"))

        basedate_ts = pd.Timestamp(BASEDATE)
        for ts, row in export_df.iterrows():
            delta = ts - basedate_ts
            total_us = int(delta.total_seconds() * 1_000_000)
            seconds = total_us // 1_000_000
            subsec = total_us % 1_000_000
            record = int(row["RECORD (RN)"])

            fobj.write(struct.pack(fhdr, seconds, subsec, record))
            for fmt, col in zip(pyformats, payload_cols, strict=False):
                fobj.write(_pack_value(fmt, row[col]))
            fobj.write(struct.pack(ffoot, 0, validation))


def _convert_csi_file_impl(input_file, output_file, output_format, quiet=True, split_window=None):
    output_format = output_format.upper()
    output_file = _normalize_output_path(output_file, output_format)
    data, _raw_meta, file_meta = _read_csi_files_impl(
        input_file,
        quiet=quiet,
        sortindex=True,
        collect_file_meta=True,
    )
    meta = _normalized_meta_from_file_meta(file_meta)

    if split_window is not None:
        dataframe = _ensure_datetime_index(data).sort_index()
        os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
        outputs = []
        for chunk, start_ts, end_ts in _iter_split_chunks(dataframe, split_window):
            outfile = _timestamped_output_path(output_file, start_ts, end_ts)
            if output_format in ["TOA5", "TOACI1"]:
                write_csi_toa5(outfile, chunk, filetype=output_format, meta=meta)
            elif output_format == "TOB1":
                write_csi_tob1(outfile, chunk, meta=meta)
            elif output_format == "TOB3":
                write_csi_tob3(outfile, chunk, meta=meta)
            elif output_format == "CSIXML":
                write_csi_csixml(outfile, chunk, meta=meta)
            else:
                raise ValueError(f"Unknown output format: {output_format}")
            outputs.append(outfile)
        return outputs

    if output_format in ["TOA5", "TOACI1"]:
        write_csi_toa5(output_file, data, filetype=output_format, meta=meta)
    elif output_format == "TOB1":
        write_csi_tob1(output_file, data, meta=meta)
    elif output_format == "TOB3":
        write_csi_tob3(output_file, data, meta=meta)
    elif output_format == "CSIXML":
        write_csi_csixml(output_file, data, meta=meta)
    else:
        raise ValueError(f"Unknown output format: {output_format}")

    return output_file


def convert_csi_file(input_file, output_file, output_format, quiet=True, split_window=None):
    if isinstance(input_file, list | tuple):
        os.makedirs(output_file, exist_ok=True)
        outputs = []
        output_format = output_format.upper()
        for one_input in input_file:
            basename = os.path.basename(one_input)
            stem, _ = os.path.splitext(basename)
            outfile = os.path.join(output_file, f"{output_format}_{stem}.dat")
            converted = _convert_csi_file_impl(
                one_input, outfile, output_format, quiet=quiet, split_window=split_window
            )
            if isinstance(converted, list):
                outputs.extend(converted)
            else:
                outputs.append(converted)
        return outputs

    return _convert_csi_file_impl(
        input_file, output_file, output_format, quiet=quiet, split_window=split_window
    )


if __name__ == "__main__":
    _emit("Internal module. Use CLI entrypoint: csiio --help")
