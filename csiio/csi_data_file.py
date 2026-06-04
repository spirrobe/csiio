from dataclasses import dataclass
from typing import Any

import pandas as pd

from ._helpers import (
    _merge_dataframes,
    _split_name_and_unit,
)


@dataclass
class CSIDataFile:
    """Stateful reader/writer wrapper around Campbell Scientific data files.

    Usage:
        one = CSIDataFile('/path/to/file.dat')
        df = one.read()
        one.write('/tmp/out.csv', 'CSV')

        many = CSIDataFile(['/path/a.dat', '/path/b.dat'])
        df_all = many.read()
        many.write('/tmp/out.csv', 'CSV', split_window='1H')

        # Initialize from DataFrame:
        df = pd.DataFrame({'value': [1, 2, 3]},
                          index=pd.date_range('2020-01-01', periods=3))
        csdf = CSIDataFile(data=df)
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

    def _meta_from_dataframe(self, df, filetype="TOA5"):
        """Build a CSI-compatible meta structure from a normalized DataFrame."""
        names = ["TIMESTAMP", "RECORD"] + [
            _split_name_and_unit(c)[0] for c in df.columns if c != "RECORD (RN)"
        ]
        units = ["TS", "RN"] + [
            _split_name_and_unit(c)[1] for c in df.columns if c != "RECORD (RN)"
        ]
        process = ["", ""] + ["Smp"] * (len(names) - 2)
        return [
            [filetype, "unknown", "unknown", "unknown", "unknown", "unknown", "unknown"],
            names,
            units,
            process,
        ]

    def read(self, meta_only=False, quiet=True, sortindex=True, max_workers=None, **kwargs):
        from .read_csi_files import _normalized_meta_from_file_meta, _read_csi_files_impl

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
                max_workers=max_workers,
                **kwargs,
            )
            if meta_only:
                normalized_meta, new_file_meta = result
                self.file_meta.update(new_file_meta)
                self.meta = _normalized_meta_from_file_meta(self.file_meta)
                return self.meta

            new_data, _new_meta, new_file_meta = result
            self.file_meta.update(new_file_meta)

            self.data = _merge_dataframes(self.data, new_data)
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
            max_workers=max_workers,
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

    def write(
        self,
        output_file,
        output_format,
        quiet=True,
        split_window=None,
        max_workers=None,
        exists_action="merge",
        closed="left",
        label="left",
        line_terminator=None,
    ):
        from .convert_csi_files import convert_csi_file

        if self.data is not None and not self.paths:
            if not isinstance(self.data, pd.DataFrame):
                raise TypeError(
                    "CSIDataFile.write requires DataFrame data. Call read() or initialize with dataframe."
                )
            if self.meta is None or not self.meta:
                self.meta = self._meta_from_dataframe(self.data, output_format)
            else:
                if self.meta and len(self.meta) > 0 and len(self.meta[0]) > 0:
                    self.meta[0][0] = output_format
            return convert_csi_file(
                self.data,
                output_file,
                output_format,
                quiet=quiet,
                split_window=split_window,
                max_workers=max_workers,
                exists_action=exists_action,
                meta=self.meta,
                closed=closed,
                label=label,
                line_terminator=line_terminator,
            )

        if not self.paths:
            raise ValueError(
                "No input path or data configured. Set CSIDataFile.paths or pass data during initialization."
            )

        if self.meta and self.meta and len(self.meta) > 0 and len(self.meta[0]) > 0:
            self.meta[0][0] = output_format
        if len(self.paths) == 1:
            return convert_csi_file(
                self.paths[0],
                output_file,
                output_format,
                quiet=quiet,
                split_window=split_window,
                max_workers=max_workers,
                exists_action=exists_action,
                meta=self.meta,
                closed=closed,
                label=label,
                line_terminator=line_terminator,
            )
        return convert_csi_file(
            self.paths,
            output_file,
            output_format,
            quiet=quiet,
            split_window=split_window,
            max_workers=max_workers,
            exists_action=exists_action,
            meta=self.meta,
            closed=closed,
            label=label,
            line_terminator=line_terminator,
        )
