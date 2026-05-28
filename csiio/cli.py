import argparse
import sys

import pandas as pd

from .read_csi_files import CSIDataFile, convert_csi_file


def _comma_or_repeatable_paths(values):
    paths = []
    for value in values:
        parts = [part.strip() for part in value.split(",") if part.strip()]
        paths.extend(parts)
    return paths


def build_parser():
    parser = argparse.ArgumentParser(
        prog="csiio",
        description="CLI for Campbell Scientific file reading, conversion, and CSV export.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_read = sub.add_parser("read", help="Read one or more files and print summary information.")
    p_read.add_argument(
        "input", nargs="+", help="One or more input files (space or comma separated)."
    )
    p_read.add_argument(
        "--meta-only",
        dest="meta_only",
        action="store_true",
        help="Read metadata only.",
    )
    p_read.add_argument("--quiet", action="store_true", help="Reduce reader log output.")
    p_read.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="Maximum worker threads for parallel read operations.",
    )
    p_read.add_argument("--no-sortindex", action="store_true", help="Do not sort DataFrame index.")
    p_read.add_argument(
        "--as-csv", action="store_true", help="Print DataFrame as CSV to stdout (for shell pipes)."
    )

    p_convert = sub.add_parser("convert", help="Convert one file to a different Campbell format.")
    p_convert.add_argument("input", help="Input file path.")
    p_convert.add_argument(
        "--output-format",
        required=True,
        choices=["TOA5", "TOACI1", "TOB1", "TOB3", "CSIXML"],
        help="Target output format.",
    )
    p_convert.add_argument("--output", required=True, help="Output file path.")
    p_convert.add_argument(
        "--split-window", default=None, help="Timedelta-like split window, e.g. 1H, 1D, 30min."
    )
    p_convert.add_argument(
        "--exists-action",
        choices=["merge", "overwrite", "skip"],
        default="overwrite",
        help="Action to perform when the output file already exists.",
    )
    p_convert.add_argument("--quiet", action="store_true", help="Reduce reader log output.")
    p_convert.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="Maximum worker threads for split-window conversion writes.",
    )

    p_csv = sub.add_parser(
        "to-csv", help="Read one or more files and export CSV (optionally split by time window)."
    )
    p_csv.add_argument(
        "input", nargs="+", help="One or more input files (space or comma separated)."
    )
    p_csv.add_argument("--output", required=True, help="Output csv path or split-file stem.")
    p_csv.add_argument(
        "--split-window", default=None, help="Timedelta-like split window, e.g. 1H, 1D, 30min."
    )
    p_csv.add_argument("--quiet", action="store_true", help="Reduce reader log output.")
    p_csv.add_argument(
        "--max-workers",
        type=int,
        default=None,
        help="Maximum worker threads for split-window CSV writes.",
    )
    p_csv.add_argument("--no-sortindex", action="store_true", help="Do not sort DataFrame index.")

    return parser


def _cmd_read(args):
    paths = _comma_or_repeatable_paths(args.input)
    reader = CSIDataFile(paths if len(paths) > 1 else paths[0])

    if args.meta_only and args.as_csv:
        raise ValueError("--as-csv cannot be used together with --meta-only.")

    result = reader.read(
        meta_only=args.meta_only,
        quiet=args.quiet,
        sortindex=not args.no_sortindex,
        max_workers=args.max_workers,
    )

    if args.meta_only:
        if isinstance(result, list):
            print(f"metadata files: {len(result)}")
        else:
            print("metadata loaded")
        return 0

    if not isinstance(result, pd.DataFrame):
        raise TypeError("read command expected DataFrame output.")

    if args.as_csv:
        result.to_csv(sys.stdout)
    else:
        print(f"dataframe shape: {result.shape}")
        print(f"columns: {len(result.columns)}")
    return 0


def _cmd_convert(args):
    output = convert_csi_file(
        args.input,
        args.output,
        args.output_format,
        quiet=args.quiet,
        split_window=args.split_window,
        max_workers=args.max_workers,
        exists_action=args.exists_action,
    )
    if isinstance(output, list):
        for out in output:
            print(out)
    else:
        print(output)
    return 0


def _cmd_to_csv(args):
    paths = _comma_or_repeatable_paths(args.input)
    reader = CSIDataFile(paths if len(paths) > 1 else paths[0])
    reader.read(quiet=args.quiet, sortindex=not args.no_sortindex, max_workers=args.max_workers)
    outputs = reader.to_csv(
        args.output, split_window=args.split_window, max_workers=args.max_workers
    )
    for out in outputs:
        print(out)
    return 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    handlers = {
        "read": _cmd_read,
        "convert": _cmd_convert,
        "to-csv": _cmd_to_csv,
    }
    handler = handlers.get(args.command)
    if handler is not None:
        return handler(args)

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
