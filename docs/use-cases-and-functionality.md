# csiio Use Cases and Functionality

This document describes typical usage patterns and the main capabilities provided by csiio (Campbell Scientific Inc. I/O) to handle data logger files from Campbell Scientific Inc. These formats are well documents at https://

## Supported File Formats

csiio is designed to work with the common Campbell Scientific formats:

- TOA5 (ASCII file with 4 header lines)
- TOACI1 (TOA5 with short header, usually handled by the same functionality as TOA5)
- TOB1 (binary file with 4 header lines where each record is one line)
- TOB3 (binary file with 4 header lines with record frames to ensure integrity; this is the native format of CSI loggers) 
- CSIXML (XML-based format)

## Main Use Cases

## 1) Read CSI Data to DataFrame

Use this when you need immediate pandas-based analysis.

CLI:

```bash
csiio read /path/to/file.dat
```

Python:

```python
from csiio import read_csi_files

data, meta = read_csi_files("/path/to/file.dat")
```

Outcome:

- Data is returned as a pandas DataFrame.
- Metadata is returned alongside the DataFrame.

## 2) Read Metadata Only

Use this when validating schema, variable names, or file headers without loading full data payloads.

CLI:

```bash
csiio read /path/to/file.dat --metaonly
```

Python:

```python
from csiio import read_csi_files

meta = read_csi_files("/path/to/file.dat", metaonly=True)
```

## 3) Convert Between CSI Formats

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

## 4) Export CSV Outputs

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
outputs = reader.to_csv("/tmp/out.csv", split_window="1H")
```

CLI equivalent:

```bash
csiio to-csv /path/to/in.dat --output /tmp/out.csv --split-window 1H
```

## 5) Stream Data Through Shell Pipelines

Use this when integrating with command-line tooling.

```bash
csiio read /path/to/in.dat --as-csv | gzip > out.csv.gz
```

## API Surface Summary

The package currently exposes:

- CSIDataFile
- read_csi_files
- read_csi_meta
- read_csi_toa5
- read_csi_tob1
- read_csi_tob3
- read_csi_csixml
- convert_csi_file
- convert_csi_files
- write_csi_toa5
- write_csi_tob1
- write_csi_tob3
- write_csi_csixml

## Notes and Constraints

- DataFrame-first pipeline: read paths are DataFrame-oriented.
- TOB3 parsing may be slower than proprietary tooling for very large files due to frame-level handling in Python.
- File type detection is automatic for supported CSI formats.
