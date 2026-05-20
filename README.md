# csiio

Python reader and converter for Campbell Scientific Inc data files either OOP or functional.

This package focuses on practical ingestion and conversion workflows for CSI formats (including TOA5, TOACI1, TOB1, TOB3, and CSIXML), with DataFrame-native handling for downstream analysis pipelines.

Citation: If you use csiio in published work and want to cite it, see the citation guidance in [How To Cite](README.md#how-to-cite).

## Install

```bash
pip install -e .
```

## Core Functionality

- Read CSI files into pandas DataFrames.
- Auto-detect file type during reads.
- Convert between supported CSI formats.
- Export single CSV outputs or time-window-split CSVs.
- Use either CLI workflows or Python API workflows.

## Changelog Policy

- Release notes live in `CHANGELOG.md`.
- Add user-visible changes under `[Unreleased]` in the section that fits best: `Added`, `Changed`, `Fixed`, `CI/Build`, or `Docs`.
- On release, run `python scripts/release_changelog.py 0.2.0` to move `[Unreleased]` into a dated version section, then tag the release.
- Use `python scripts/release_changelog.py 0.2.0 --dry-run` to preview the edit without writing.


## Python API Usage

```python
from csiio import CSIDataFile, read_csi_files, convert_csi_file
import pandas as pd

# High-level object workflow
reader = CSIDataFile(["a.dat", "b.dat"])
df = reader.read()
normalized_meta = reader.meta
per_file_meta = reader.file_meta
csv_files = reader.to_csv("/tmp/out.csv", split_window="1h")

# Functional workflow
df2, meta = read_csi_files("/path/to/file.dat")
df_many, meta_many = read_csi_files(["/path/to/a.dat", "/path/to/b.dat"], max_workers=2)
converted = convert_csi_file("/path/to/in.dat", "/tmp/TOA5_out.dat", "TOA5")
split_outputs = convert_csi_file("/path/to/in.dat", "/tmp/TOA5_out.dat", "TOA5", split_window="1h")
split_outputs_limited = convert_csi_file(
    "/path/to/in.dat", "/tmp/TOA5_out.dat", "TOA5", split_window="1h", max_workers=2
)

# Initialize from an existing pandas DataFrame
frame = pd.DataFrame(
    {"air_temp (degC)": [12.1, 12.4, 12.8]},
    index=pd.date_range("2024-01-01 00:00:00", periods=3, freq="30min"),
)
from_df = CSIDataFile(data=frame)
csv_files = from_df.to_csv("/tmp/out.csv")
split_csv_files = from_df.to_csv("/tmp/out.csv", split_window="1h", max_workers=2)
converted_file = from_df.convert("/tmp/out.dat", "TOB3", max_workers=2)
```

## CLI (command line) Usage

```bash
csiio --help

# Read and print DataFrame summary
csiio read /path/to/file.dat

# Read metadata only, useful to figure out contained columns
csiio read /path/to/file.dat --meta-only

# Stream CSV to stdout (good for shell pipelines)
csiio read /path/to/file.dat --as-csv

# Read many files with explicit worker limit
csiio read /path/to/a.dat /path/to/b.dat --max-workers 2

# Convert to another CSI format
csiio convert /path/to/in.dat --output-format TOB3 --output /tmp/TOB3_out.dat

# Convert to another CSI format and split by timewindow, useful for e.g. EC processing
csiio convert /path/to/in.dat --output-format TOB1 --split-window 1h --output /tmp/TOB1_out.dat

# Split conversion with explicit worker limit
csiio convert /path/to/in.dat --output-format TOB1 --split-window 1h --output /tmp/TOB1_out.dat --max-workers 2

# Export CSV and split by time window where time window is one of pandas known frequency strings found at https://pandas.pydata.org/docs/user_guide/timeseries.html#dateoffset-objects
csiio to-csv /path/to/in.dat --output /tmp/out.csv --split-window 1h

# Split CSV export with explicit worker limit
csiio to-csv /path/to/in.dat --output /tmp/out.csv --split-window 1h --max-workers 2
```

# Typical Use Cases and Functionality

## Supported File Formats

csiio is designed to work with the common Campbell Scientific formats where for most 4 header lines are available describing the logger (l1), the columnname (l2), the unit (l3) and the aggregation (l4):

| Format | Description | Pro | Con |
| --- | --- | --- | --- |
| TOA5 | ASCII file | Human-readable | file size
| TOACI1 | TOA5 with shorter header | Human-readable | file size
| TOB1 | Binary file with one timestamp per line | File size small | binary
| TOB3 | Binary file with 4 header lines with record frames to ensure integrity; native format of CSI loggers | Integrity | binary and complex |
| CSIXML | XML-based format | More descriptive | tree-structure not that useful for many data-related tasks

## Main Use Cases

## 1) Read CSI Data to DataFrame

Use this when you need immediate pandas-based analysis from Python.
The CLI shows a summary by default; use `--as-csv` if you want the data on stdout.

CLI summary:

```bash
csiio read /path/to/file.dat
```

CLI data stream:

```bash
csiio read /path/to/file.dat --as-csv
```

Python:

```python
from csiio import read_csi_files

# read one file
data, meta = read_csi_files("/path/to/file.dat")

# read many files
data, meta = read_csi_files(["/path/to/file.dat", "/path/to/another/file.dat"])
```

Outcome:

- Data is returned as a pandas DataFrame.
- `read_csi_files(...)` returns per-file metadata alongside the DataFrame.
- `CSIDataFile.meta` stores normalized metadata for the combined in-memory DataFrame.
- `CSIDataFile.file_meta` stores per-file metadata as a dictionary keyed by filename.

## 2) Read Metadata Only

Use this when validating variable names or file headers without loading full data payloads.

CLI:

```bash
csiio read /path/to/file.dat --meta-only
```

Python:

```python
from csiio import read_csi_files

meta = read_csi_files("/path/to/file.dat", meta_only=True)
```

When using `CSIDataFile`, `read(meta_only=True)` updates:

- `reader.meta` with normalized metadata for the current in-memory view
- `reader.file_meta` with per-file metadata keyed by filename

## 3) Convert to another CSI format

Use this when standardizing logger output or generating downstream-compatible files.

CLI:

```bash
csiio convert /path/to/in.dat --output-format TOB3 --output /tmp/out.dat
```

Python:

```python
from csiio import convert_csi_file

out = convert_csi_file("/path/to/in.dat", "/tmp/out.dat", "TOB3")
```

## 4) Convert to another CSI format and split by frequency

Use this when standardizing logger output or generating downstream-compatible files.

CLI:

```bash
csiio convert /path/to/in.dat --output-format TOB3 --output /tmp/out.dat --split-window 1h
```

Python:

```python
from csiio import convert_csi_file

outputs = convert_csi_file("/path/to/in.dat", "/tmp/out.dat", "TOB3", split_window="1h")

# Limit split-window writer threads
outputs_limited = convert_csi_file(
    "/path/to/in.dat", "/tmp/out.dat", "TOB3", split_window="1h", max_workers=2
)
```


## 5) Export CSV Outputs

Use this when delivering data to tools that expect CSV.

Single-file export (Python):

```python
from csiio import CSIDataFile

reader = CSIDataFile("/path/to/in.dat")
reader.read()
outputs = reader.to_csv("/tmp/out.csv")
```

Time-window split export (Python):

```python
outputs = reader.to_csv("/tmp/out.csv", split_window="1h")
# Limit split-window writer threads
outputs_limited = reader.to_csv("/tmp/out.csv", split_window="1h", max_workers=2)
```

CLI equivalent:

```bash
csiio to-csv /path/to/in.dat --output /tmp/out.csv --split-window 1h
```

## 6) Initialize from a pandas DataFrame

Use this when your data is already in pandas and you want to export or convert it without first writing an intermediate CSI file.

Python:

```python
import pandas as pd
from csiio import CSIDataFile

frame = pd.DataFrame(
    {
        "air_temp (degC)": [12.1, 12.4, 12.8],
        "co2_flux (umol m-2 s-1)": [0.01, -0.02, 0.03],
    },
    index=pd.date_range("2024-01-01 00:00:00", periods=3, freq="30min"),
)

reader = CSIDataFile(data=frame)

# Export CSV directly
csv_files = reader.to_csv("/tmp/out.csv")

# Export split CSV files
split_csv_files = reader.to_csv("/tmp/out.csv", split_window="1h")

# Convert directly to a CSI format
tob3_file = reader.convert("/tmp/out.dat", "TOB3")
```

Outcome:

- `CSIDataFile` normalizes the DataFrame to a `TIMESTAMP` index and adds `RECORD (RN)` when missing.
- `reader.meta` is auto-generated from the DataFrame columns.
- You can export CSV or CSI files directly from the in-memory DataFrame.

## Parallelism Controls

- Parallel workers are enabled by default for:
    - reading multiple files via list input
    - split-window conversion writes
    - split-window CSV writes
- Default worker count is `max(1, cpu_count // 4)`, capped by number of tasks.
- You can override with `max_workers` in Python API calls and `--max-workers` in CLI commands.
- Safety check: `max_workers` must be an integer between `1` and available CPU count.

## 7) Stream Data Through Shell Pipelines

Use this when integrating with command-line tooling.

```bash
csiio read /path/to/in.dat --as-csv | gzip > out.csv.gz
```

## API Surface Summary

The package currently exposes:

#### Instance
- CSIDataFile
#### Instance / Reading
- read_csi_files
- read_csi_meta
- read_csi_toa5
- read_csi_tob1
- read_csi_tob3
- read_csi_csixml
#### Conversion
- convert_csi_file
#### Writing
- write_csi_toa5
- write_csi_tob1
- write_csi_tob3
- write_csi_csixml

## Notes and Constraints

- Pandas dataframe are cnetral, csiio keeps its data internally as dataframe giving users the ease of use of pandas
- The main purpose of csiio is the reader functionality for the binary, frame-oriented TOB3 format (which may be slower than proprietary tooling for very large files due to frame-level handling in Python.) and the utility to directly convert in a pipeline instead of via cardconvert

## How To Cite

If csiio supports published work, and you have the opportunity to cite it you may cite the software repository and version you used.

Suggested BibTeX entry:

```bibtex
@software{csiio,
	title = {csiio: Reader and Converter for Campbell Scientific Data Files},
	author = {Spirig, Robert},
	year = {2026},
	url = {https://github.com/spirrobe/csiio},
	version = {0.1.0}
}
```

If relevant/available for your workflow, include a commit hash or release tag for exact reproducibility.

## Testing

Generate dual-status conversion audits (strict plus tolerance) with reason codes:

```bash
python tests/generate_conversion_audit.py
```

Outputs are written to:

- `tests/reports/conversion_audit_dual.csv`
- `tests/reports/conversion_audit_dual_summary.csv`
