"""CLI adapter around the incoming project-analyzer port."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pyarchgraph.adapters.driving.cli.configuration import TomlOptionsReader
from pyarchgraph.adapters.driving.cli.rendering import JsonReportRenderer
from pyarchgraph.application.exceptions import ExtensionError
from pyarchgraph.application.ports.analysis import ProjectAnalyzer
from pyarchgraph.application.requests import AnalysisRequest
from pyarchgraph.application.results import AnalysisReport
from pyarchgraph.domain.models import Severity


class CliExitCodePolicy:
    """Translate analysis completeness and gate findings into a CLI exit code."""

    def exit_code(self, report: AnalysisReport) -> int:
        return (
            2
            if report.status == "incomplete"
            else int(
                any(
                    item.severity is Severity.ERROR
                    for item in report.selected_view.findings
                )
            )
        )


class CliApplication:
    """Run analysis from command-line choices and present the resulting report."""

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
            "--package-max-depth",
            type=int,
            metavar="N",
            help="cap package grouping at N dotted name parts (default: immediate packages)",
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
                package_max_depth=args.package_max_depth,
                base_dir=base_dir,
            )
            report = self.analyzer.analyse(
                AnalysisRequest(
                    tuple(args.source_roots), options=options, base_dir=base_dir
                )
            )
            output = self.renderer.render(report)
        except (OSError, ValueError, ExtensionError) as error:
            print(f"pyarchgraph: {error}", file=sys.stderr)
            return 2
        sys.stdout.write(output)
        return CliExitCodePolicy().exit_code(report)
