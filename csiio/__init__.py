"""csiio package."""

from .convert_csi_files import convert_csi_file
from .csi_data_file import CSIDataFile
from .read_csi_files import (
    read_csi_csixml,
    read_csi_files,
    read_csi_meta,
    read_csi_toa5,
    read_csi_tob1,
    read_csi_tob3,
)
from .write_csi_files import (
    write_csi_ascii,
    write_csi_csixml,
    write_csi_tob1,
    write_csi_tob3,
)

__all__ = [
    "CSIDataFile",
    "read_csi_files",
    "read_csi_meta",
    "read_csi_toa5",
    "read_csi_tob1",
    "read_csi_tob3",
    "read_csi_csixml",
    "convert_csi_file",
    "write_csi_ascii",
    "write_csi_tob1",
    "write_csi_tob3",
    "write_csi_csixml",
]

__version__ = "0.2.1"
