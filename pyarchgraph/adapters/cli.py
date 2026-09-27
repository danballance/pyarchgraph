"""CLI adapter around the incoming project-analyzer port."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pyarchgraph.application.ports import ProjectAnalyzer
from pyarchgraph.domain.errors import ExtensionError
from pyarchgraph.adapters.configuration import TomlOptionsReader
from pyarchgraph.adapters.rendering import JsonReportRenderer


class CliApplication:
    def __init__(
        self,
        analyzer: ProjectAnalyzer,
        options_reader: TomlOptionsReader,
        renderer: JsonReportRenderer,
        gates: tuple[str, ...],
    ) -> None:
        self.analyzer = analyzer
        self.options_reader = options_reader
        self.renderer = renderer
        self.gates = gates

    def run(self, argv: Sequence[str] | None = None) -> int:
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
            choices=self.gates,
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
            base_dir = Path.cwd()
            options = self.options_reader.load(
                args.config,
                excludes=tuple(args.exclude),
                gate=args.gate,
                details=args.details,
                base_dir=base_dir,
            )
            report = self.analyzer.analyse(
                tuple(args.source_roots), options=options, base_dir=base_dir
            )
            output = self.renderer.render(report)
        except (OSError, ValueError, ExtensionError) as error:
            print(f"pyarchgraph: {error}", file=sys.stderr)
            return 2
        sys.stdout.write(output)
        return report.exit_code
