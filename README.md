# csiio

Python reader and converter for Campbell Scientific Inc data files.

This package focuses on practical ingestion and conversion workflows for CSI formats (including TOA5, TOACI1, TOB1, TOB3, and CSIXML), with DataFrame-native handling for downstream analysis pipelines.

Citation: If you use csiio in published work, see the citation guidance in [How To Cite](README.md#how-to-cite).

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

## Documentation

- Use Cases and Functionality: [docs/use-cases-and-functionality.md](docs/use-cases-and-functionality.md)
- Citation Guidance: [How To Cite](README.md#how-to-cite)

## CLI Usage

```bash
csiio --help

# Read and print DataFrame summary
csiio read /path/to/file.dat

# Read metadata only
csiio read /path/to/file.dat --metaonly

# Stream CSV to stdout (good for shell pipelines)
csiio read /path/to/file.dat --as-csv

# Convert to another CSI format
csiio convert /path/to/in.dat --output-format TOB3 --output /tmp/TOB3_out.dat

# Export CSV and split by time window
csiio to-csv /path/to/in.dat --output /tmp/out.csv --split-window 1H
```

## Python API Usage

```python
from csiio import CSIDataFile, read_csi_files, convert_csi_file

# High-level object workflow
reader = CSIDataFile(["a.dat", "b.dat"])
df = reader.read()
csv_files = reader.to_csv("/tmp/out.csv", split_window="1D")

# Functional workflow
df2, meta = read_csi_files("/path/to/file.dat")
converted = convert_csi_file("/path/to/in.dat", "/tmp/TOA5_out.dat", "TOA5")
```

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

Ifr relevant/available for your workflow, include a commit hash or release tag for exact reproducibility.

## Conversion Audit Reports

Generate dual-status conversion audits (strict plus tolerance) with reason codes:

```bash
python tests/generate_conversion_audit.py
```

Outputs are written to:

- `tests/reports/conversion_audit_dual.csv`
- `tests/reports/conversion_audit_dual_summary.csv`
