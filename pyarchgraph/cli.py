"""Check explicit import roots and write a scoped JSON report to stdout."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pyarchgraph.analysis import analyse
from pyarchgraph.configuration import load_options
from pyarchgraph.rendering import render_json


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pyarchgraph",
        allow_abbrev=False,
        description="Check Python import dependencies with explicit coverage and context.",
    )
    parser.add_argument(
        "source_roots",
        type=Path,
        nargs="+",
        metavar="ROOT",
        help="explicit import root(s), for example . src",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="exclude a POSIX-relative glob in each root; repeatable; tests excluded",
    )
    parser.add_argument(
        "--gate",
        choices=("structural", "non-typing", "module-body"),
        default="structural",
        help="view determining the CI result (default: structural)",
    )
    parser.add_argument(
        "--details",
        choices=("summary", "component-edges"),
        default="summary",
        help="include a witness or all internal component dependencies",
    )
    parser.add_argument(
        "--config", type=Path, help="explicit TOML ownership/boundary configuration"
    )
    args = parser.parse_args(argv)
    try:
        options = load_options(
            args.config,
            excludes=tuple(args.exclude),
            gate=args.gate,
            details=args.details,
        )
        report = analyse(tuple(args.source_roots), options=options)
        output = render_json(report)
    except (OSError, ValueError) as error:
        print(f"pyarchgraph: {error}", file=sys.stderr)
        return 2
    sys.stdout.write(output)
    return report.exit_code
