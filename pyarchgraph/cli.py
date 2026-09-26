"""Check one import root and write its findings as JSON on stdout."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pyarchgraph.analysis import analyse
from pyarchgraph.rendering import render_json


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pyarchgraph",
        allow_abbrev=False,
        description="Check Python import statements for cycles and unresolved imports.",
    )
    parser.add_argument(
        "source_root",
        type=Path,
        metavar="SOURCE_ROOT",
        help="explicit import root (for a src layout, pass src)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="exclude a POSIX-relative path glob; repeatable; tests are always excluded",
    )
    args = parser.parse_args(argv)
    try:
        report = analyse(args.source_root, excludes=tuple(args.exclude))
        output = render_json(report)
    except (OSError, ValueError) as error:
        print(f"pyarchgraph: {error}", file=sys.stderr)
        return 2
    sys.stdout.write(output)
    return 1 if report.findings else 0
