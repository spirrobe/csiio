import logging
import os
import struct
from concurrent.futures import ThreadPoolExecutor
from xml.sax.saxutils import escape

import pandas as pd

try:
    import tqdm
except ImportError:
    tqdm = None

from ._helpers import (
    BASEDATE,
    _emit,
    _ensure_datetime_index,
    _infer_struct_format,
    _iter_split_chunks,
    _pack_value,
    _prepare_export_dataframe,
    _prepare_output_for_existing,
    _resolve_meta_header_value,
    _resolve_meta_process_values,
    _resolve_parallel_workers,
    _resolve_split_group_freq,
    _split_name_and_unit,
    _timestamped_output_path,
    meta2binary_csiformats,
    read_csi_formats,
)

LOGGER = logging.getLogger("csiio")


def _quoted_fields(values):
    return ",".join(f'"{x}"' for x in values)


def _empty_quoted_fields(count):
    return ",".join('""' for _ in range(count))


def _compose_ascii_header(
    filetype, station, logger, serial, osversion, program, table, names, units
):
    header = [
        _quoted_fields([filetype, station, logger, serial, osversion, program, table]),
        _quoted_fields(names),
        _quoted_fields(units),
    ]
    if filetype == "TOA5":
        header.append(_empty_quoted_fields(len(names)))
    return header


def _write_csi_file(
    output_file, dataframe, output_format, meta=None, line_terminator=None, quiet=True
):
    if not quiet:
        _emit(f"Writing {output_format} file: {output_file}")
        _emit(f"  Records: {len(dataframe)}", quiet=quiet)
        _emit(f"  Payload columns: {', '.join(dataframe.columns)}", quiet=quiet)
    if output_format in ["TOA5", "TOACI1", "CSV"]:
        write_csi_ascii(
            output_file,
            dataframe,
            filetype=output_format,
            meta=meta,
            line_terminator=line_terminator,
        )
    elif output_format == "TOB1":
        write_csi_tob1(
            output_file,
            dataframe,
            meta=meta,
            line_terminator=line_terminator,
            quiet=quiet,
        )
    elif output_format == "TOB3":
        write_csi_tob3(
            output_file,
            dataframe,
            meta=meta,
            line_terminator=line_terminator,
            quiet=quiet,
        )
    elif output_format == "CSIXML":
        write_csi_csixml(
            output_file,
            dataframe,
            meta=meta,
            line_terminator=line_terminator,
            quiet=quiet,
        )
    else:
        raise ValueError(f"Unknown output format: {output_format}")
    return output_file


def write_csi_files(
    output_file,
    dataframe,
    output_format,
    split_window=None,
    max_workers=None,
    exists_action="merge",
    quiet=True,
    meta=None,
    line_terminator=None,
    closed="left",
    label="left",
):
    output_format = output_format.upper()
    line_terminator = line_terminator if line_terminator is not None else os.linesep

    if split_window is None:
        dataframe = _prepare_output_for_existing(output_file, dataframe, exists_action, quiet=quiet)
        if dataframe is None:
            return output_file
        return _write_csi_file(
            output_file,
            dataframe,
            output_format,
            meta=meta,
            line_terminator=line_terminator,
            quiet=quiet,
        )

    dataframe = _ensure_datetime_index(dataframe).sort_index()
    group_freq = _resolve_split_group_freq(split_window)
    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
    chunk_tasks = []
    for chunk, start_ts, end_ts in _iter_split_chunks(
        dataframe, group_freq, closed=closed, label=label
    ):
        outfile = _timestamped_output_path(
            output_file, start_ts.floor(group_freq), end_ts.ceil(group_freq)
        )
        chunk_tasks.append((chunk, outfile))

    worker_count = _resolve_parallel_workers(len(chunk_tasks), max_workers=max_workers)

    def _write_split_chunk(task):
        chunk, outfile = task
        if exists_action == "skip" and os.path.exists(outfile):
            return outfile
        if exists_action == "merge" and os.path.exists(outfile):
            chunk = _prepare_output_for_existing(outfile, chunk, exists_action, quiet=quiet)
        return _write_csi_file(
            outfile, chunk, output_format, meta=meta, line_terminator=line_terminator, quiet=quiet
        )

    if not quiet:
        _emit(f"Writing {len(chunk_tasks)} split files with {worker_count} worker(s)...")
    if worker_count == 1:
        return [_write_split_chunk(task) for task in chunk_tasks]

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        if tqdm is not None and not quiet:
            return list(executor.map(_write_split_chunk, chunk_tasks))
        else:
            return list(
                tqdm.tqdm(
                    executor.map(_write_split_chunk, chunk_tasks),
                    total=len(chunk_tasks),
                    disable=quiet,
                )
            )


def write_csi_ascii(
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
    line_terminator=None,
    quiet=True,
):
    if filetype not in ["TOA5", "TOACI1", "CSV"]:
        raise ValueError("filetype must be TOA5, TOACI1, or CSV")
    line_terminator = line_terminator if line_terminator is not None else os.linesep

    export_df = _prepare_export_dataframe(dataframe)

    station = _resolve_meta_header_value(meta, 0, 1, station)
    logger = _resolve_meta_header_value(meta, 0, 2, logger)
    serial = _resolve_meta_header_value(meta, 0, 3, serial)
    osversion = _resolve_meta_header_value(meta, 0, 4, osversion)
    program = _resolve_meta_header_value(meta, 0, 5, program)
    table = _resolve_meta_header_value(meta, 0, 6, table)

    raw_names = ["TIMESTAMP"] + list(export_df.columns)
    names, units = zip(*[_split_name_and_unit(col) for col in raw_names], strict=False)
    header = _compose_ascii_header(
        filetype,
        station,
        logger,
        serial,
        osversion,
        program,
        table,
        names,
        units,
    )

    kwargs = {
        "index": True,
        "float_format": "%.6g",
        "escapechar": "\\",
        "lineterminator": line_terminator,
    }
    if not quiet:
        _emit(f"Writing {filetype} file: {outfile}")
        _emit(f"  Records: {len(export_df)}")
        _emit(f"  Payload columns: {', '.join(names[1:])}")
        _emit(f"  Payload units: {', '.join(units[1:])}")
    if filetype != "CSV":
        with open(outfile, "w", encoding="utf-8", newline="") as fobj:
            fobj.write(line_terminator.join(header) + line_terminator)

        write_df = export_df.copy()
        write_df.index = (
            write_df.index.strftime("%Y-%m-%d %H:%M:%S.%f").str.rstrip("0").str.rstrip(".")
        )
        write_df.to_csv(
            outfile,
            mode="a",
            header=False,
            **kwargs,
        )
    else:
        export_df.to_csv(outfile, **kwargs)  # Write CSV without header


def write_csi_csixml(
    outfile, dataframe, process="Smp", meta=None, line_terminator=None, quiet=True
):
    line_terminator = line_terminator if line_terminator is not None else os.linesep

    def _xml_safe_text(value):
        if pd.isna(value):
            return ""
        text = str(value).replace("\x00", "")
        text = text.replace("\r", "").replace("\n", "")
        return "".join(ch for ch in text if ch == "\t" or ord(ch) >= 0x20)

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
    if not quiet:
        _emit(f"Writing CSIXML file: {outfile}")
        _emit(f"  Records: {len(export_df)}")
        _emit(f"  Payload columns: {', '.join([name for name, _ in split_names])}")
        _emit(f"  Payload units: {', '.join([unit for _, unit in split_names])}")
        _emit(f"  Payload processes: {', '.join(process_values)}")

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
        fobj.write(line_terminator.join(lines) + line_terminator)


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
    line_terminator=None,
    quiet=True,
):
    export_df = _prepare_export_dataframe(dataframe).sort_index()
    line_terminator = line_terminator if line_terminator is not None else os.linesep
    station = _resolve_meta_header_value(meta, 0, 1, station)
    logger = _resolve_meta_header_value(meta, 0, 2, logger)
    serial = _resolve_meta_header_value(meta, 0, 3, serial)
    osversion = _resolve_meta_header_value(meta, 0, 4, osversion)
    program = _resolve_meta_header_value(meta, 0, 5, program)
    table = _resolve_meta_header_value(meta, 0, 6, table)
    basefields = ["ULONG", "ULONG", "ULONG"]
    payload_cols = [c for c in export_df.columns if c != "RECORD (RN)"]
    payload_names_units = [_split_name_and_unit(c) for c in payload_cols]
    payload_names = [name for name, _ in payload_names_units]
    payload_units = [unit for _, unit in payload_names_units]
    #  this first branch only works for the binary TOB1/TOB3
    payload_formats = (
        meta2binary_csiformats(meta, columns=payload_names)
        if meta and meta[0][0] == "TOB1"
        else {c: _infer_struct_format(export_df[c], tob3=False) for c in payload_cols}
    )
    pyformats = read_csi_formats(basefields + list(payload_formats.values()))

    header = [
        _quoted_fields(["TOB1", station, logger, serial, osversion, f"CPU:{program}", "0", table]),
        _quoted_fields(["SECONDS", "NANOSECONDS", "RECORD"] + payload_names),
        _quoted_fields(["SECONDS", "NANOSECONDS", "RN"] + payload_units),
        _empty_quoted_fields(len(["SECONDS", "NANOSECONDS", "RECORD"]))
        + ("," if payload_names else "")
        + _quoted_fields(["Smp" for _ in payload_names]),
        _quoted_fields(basefields + list(payload_formats.values())),
    ]
    if not quiet:
        _emit(f"Writing TOB1 file: {outfile}")
        _emit(f"  Records: {len(export_df)}")
        _emit(f"  Payload columns: {', '.join(payload_names)}")
        _emit(f"  Payload formats: {', '.join(payload_formats.values())}")
    with open(outfile, "wb") as fobj:
        fobj.write((line_terminator.join(header) + line_terminator).encode("utf-8"))

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
    line_terminator=None,
    quiet=True,
):
    export_df = _prepare_export_dataframe(dataframe).sort_index()
    line_terminator = line_terminator if line_terminator is not None else os.linesep

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
        _quoted_fields(["TOB3", station, logger, serial, osversion, f"CPU:{program}", "0", table]),
        _quoted_fields(
            [
                table,
                "1 SEC",
                str(framesize),
                str(len(export_df)),
                str(validation),
                "Sec1Usec",
                "0",
                "0",
                "0",
            ]
        ),
        _quoted_fields(payload_names),
        _quoted_fields(payload_units),
        _quoted_fields(["Smp" for _ in payload_names]),
        _quoted_fields(payload_formats),
    ]
    if not quiet:
        _emit(f"Writing TOB3 file: {outfile}")
        _emit(f"  Framesize: {framesize} bytes")
        _emit(f"  Records: {len(export_df)}")
        _emit(f"  Payload columns: {', '.join(payload_names)}")
        _emit(f"  Payload formats: {', '.join(payload_formats)}")
    with open(outfile, "wb") as fobj:
        fobj.write((line_terminator.join(header) + line_terminator).encode("utf-8"))

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
