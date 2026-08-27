import logging
import os

import pandas as pd

from ._helpers import (
    _emit,
    _normalize_output_path,
    _resolve_parallel_workers,
)
from .write_csi_files import write_csi_files

LOGGER = logging.getLogger("csiio")


def _convert_csi_file_impl(
    input_file,
    output_file,
    output_format,
    quiet=True,
    split_window=None,
    max_workers=None,
    exists_action="merge",
    meta=None,
    closed="left",
    label="left",
    line_terminator=None,
    columns=None,
):
    line_terminator = line_terminator if line_terminator is not None else os.linesep
    output_format = output_format.upper()
    original_output_file = output_file
    normalized_output_file = _normalize_output_path(output_file, output_format)
    if os.path.exists(original_output_file):
        output_file = original_output_file
    elif os.path.exists(normalized_output_file):
        output_file = normalized_output_file
    else:
        output_file = normalized_output_file

    if isinstance(input_file, pd.DataFrame):
        data = input_file
    else:
        from .read_csi_files import _normalized_meta_from_file_meta, _read_csi_files_impl

        data, _raw_meta, file_meta = _read_csi_files_impl(
            input_file,
            quiet=quiet,
            sortindex=True,
            collect_file_meta=True,
            max_workers=max_workers,
            columns=columns,
        )
        meta = _normalized_meta_from_file_meta(file_meta)
    if not quiet:
        _emit(
            f"Converting {len(data)} rows and {len(data.columns)} columns to {output_format} format",
            quiet=quiet,
        )
    return write_csi_files(
        output_file,
        data,
        output_format,
        split_window=split_window,
        max_workers=max_workers,
        exists_action=exists_action,
        quiet=quiet,
        meta=meta,
        closed=closed,
        label=label,
        line_terminator=line_terminator,
        columns=columns,
    )


def convert_csi_file(
    input_file,
    output_file,
    output_format,
    quiet=True,
    split_window=None,
    max_workers=None,
    exists_action="merge",
    meta=None,
    closed="left",
    label="left",
    line_terminator=None,
    columns=None,
):
    output_format = output_format.upper()
    if not quiet:
        _emit(
            f"Converting {input_file} to {output_format} format and saving to {output_file}",
            quiet=quiet,
        )
    line_terminator = line_terminator if line_terminator is not None else os.linesep
    if isinstance(input_file, list | tuple):
        _resolve_parallel_workers(len(input_file), max_workers=max_workers)
        os.makedirs(output_file, exist_ok=True)
        outputs = []
        for one_input in input_file:
            basename = os.path.basename(one_input)
            stem, _ = os.path.splitext(basename)
            ext = ".csv" if output_format == "CSV" else ".dat"
            outfile = os.path.join(output_file, f"{output_format}_{stem}{ext}")
            # take the outfile from the user if it is not the same as the output_file directory
            if output_file != outfile:
                if os.sep not in output_file:
                    outfolder = os.path.dirname(one_input)
                    outfile = os.path.join(outfolder, f"{output_format}_{stem}{ext}")
                else:
                    outfolder, outfile = os.path.split(output_file)
                    outfile = f"{outfolder}/{output_format}_{outfile}{ext}"

            if not quiet:
                if split_window is not None:
                    _emit(
                        f"Converting {one_input} to {output_format} format and saving to {outfile.replace(ext,'_STARTDATE_STARTTIME_ENDDATE_ENDTIME'+ext).replace('__','_')} with split_window={split_window}",
                        quiet=quiet,
                    )
                else:
                    _emit(
                        f"Converting {one_input} to {output_format} format and saving to {outfile}",
                        quiet=quiet,
                    )
            converted = _convert_csi_file_impl(
                one_input,
                outfile,
                output_format,
                quiet=quiet,
                split_window=split_window,
                max_workers=max_workers,
                exists_action=exists_action,
                meta=meta,
                closed=closed,
                label=label,
                line_terminator=line_terminator,
                columns=columns,
            )
            if isinstance(converted, list):
                outputs.extend(converted)
            else:
                outputs.append(converted)
        if not quiet:
            _emit(
                f"Converted {len(outputs)} files to {output_format} format and saved to {output_file}",
                quiet=quiet,
            )
        return outputs

    return _convert_csi_file_impl(
        input_file,
        output_file,
        output_format,
        quiet=quiet,
        split_window=split_window,
        max_workers=max_workers,
        exists_action=exists_action,
        meta=meta,
        closed=closed,
        label=label,
        line_terminator=line_terminator,
        columns=columns,
    )
