import logging
import os
import struct
import xml.etree.ElementTree as ElementTree
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from ._helpers import (
    BASEDATE,
    _resolve_parallel_workers,
    read_csi_formats,
)

__author__ = "spirrobe"

LOGGER = logging.getLogger(__name__)


def _emit(message, level="info", quiet=False):
    log_func = getattr(LOGGER, level, LOGGER.info)
    log_func(message)
    if quiet and level == "info":
        return
    if not LOGGER.hasHandlers():
        print(message)


_DEFAULT_HEADER_VALUES = (
    "TOA5",
    "converted",
    "converted",
    "converted",
    "converted",
    "converted",
    "converted",
)


def _normalize_requested_columns(columns):
    if columns is None:
        return None
    if isinstance(columns, str | bytes):
        return [columns]
    if hasattr(columns, "__iter__"):
        columns_list = list(columns)
    else:
        raise TypeError("columns must be a string or sequence of strings")

    if not all(isinstance(col, str | bytes) for col in columns_list):
        raise TypeError("columns must be a string or sequence of strings")
    return [str(col) for col in columns_list]


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
    formats = [
        merged_fields.get("TIMESTAMP", {}).get("type", ""),
        merged_fields.get("RECORD", {}).get("type", ""),
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
        formats.append(field.get("type", ""))
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
        formats,
    ]


def _read_csi_files_impl(
    filename,
    meta_only=False,
    quiet=True,
    sortindex=True,
    collect_file_meta=False,
    max_workers=None,
    columns=None,
    _raise_if_no_columns=True,
    **kwargs,
):
    # Backward compatibility: ignore legacy non-DataFrame flags.
    requested_as_dataframe = kwargs.pop("asdataframe", True)
    if requested_as_dataframe is False and not quiet:
        _emit(
            "Ignoring asdataframe=False: read path is DataFrame-only.", level="warning", quiet=quiet
        )

    if isinstance(filename, list):
        worker_count = _resolve_parallel_workers(len(filename), max_workers=max_workers)

        def _read_single_file(file):
            if isinstance(filename, list) and len(filename) > 1:
                raise_if_no_columns = False
            else:
                raise_if_no_columns = _raise_if_no_columns
            return _read_csi_files_impl(
                file,
                meta_only=meta_only,
                quiet=quiet,
                sortindex=sortindex,
                collect_file_meta=collect_file_meta,
                max_workers=max_workers,
                columns=columns,
                _raise_if_no_columns=raise_if_no_columns,
                **kwargs,
            )

        if worker_count == 1:
            results = [_read_single_file(file) for file in filename]
        else:
            with ThreadPoolExecutor(max_workers=worker_count) as executor:
                results = list(executor.map(_read_single_file, filename))

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
            _emit("Reading the file " + str(filename), quiet=quiet)

        requested_columns = _normalize_requested_columns(columns)

        if filetype in ["TOA5", "TOB1", "TOACI1", "TOB3", "CSIXML"]:
            if not quiet:
                _emit(str(filename) + " is a " + filetype + "-File", quiet=quiet)
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
            if requested_columns is not None and data is not False:
                present_columns = [col for col in requested_columns if col in data.columns]
                missing_columns = [col for col in requested_columns if col not in data.columns]
                if not present_columns:
                    message = f"{filename}: none of the requested columns are present: {requested_columns}"
                    if _raise_if_no_columns:
                        raise ValueError(message)
                    _emit(message, level="warning", quiet=quiet)
                elif missing_columns:
                    _emit(
                        f"{filename}: requested columns not all present. present={present_columns}, missing={missing_columns}. Remaining data will still be returned.",
                        level="warning",
                        quiet=quiet,
                    )
                data = data.reindex(columns=requested_columns)

            if collect_file_meta:
                return data, meta, per_file_meta
            return data, meta

        else:
            if not quiet:
                _emit("Neither TOA5,TOB1, TOB3 not CSIXML-File", quiet=quiet)
            return False, False


def read_csi_files(
    filename,
    meta_only=False,
    quiet=True,
    sortindex=True,
    max_workers=None,
    columns=None,
    **kwargs,
):
    return _read_csi_files_impl(
        filename,
        meta_only=meta_only,
        quiet=quiet,
        sortindex=sortindex,
        max_workers=max_workers,
        columns=columns,
        **kwargs,
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
            if ii == "L":
                ii = ">L"
            nbyte = struct.Struct(ii).size
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


if __name__ == "__main__":
    _emit("Internal module. Use CLI entrypoint: csiio --help")
