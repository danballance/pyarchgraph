"""Command-line interface for pyarchgraph."""

from __future__ import annotations

import argparse
from contextlib import suppress
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from pyarchgraph.analysis import DEFAULT_PACKAGE_DEPTH, analyse
from pyarchgraph.model import View
from pyarchgraph.projection import MINIMUM_PACKAGE_DEPTH
from pyarchgraph.model import Diagnostic
from pyarchgraph.findings import check_status
from pyarchgraph.policy import GraphPolicy
from pyarchgraph.provenance import compare_baseline
from pyarchgraph.rendering import (
    ImpliedEdges,
    render_json,
    render_mermaid_markdown,
    render_quality_summary,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pyarchgraph",
        description=(
            "Statically analyse internal Python imports and emit JSON data, "
            "a Mermaid graph report, or an architecture score summary."
        ),
    )
    parser.add_argument(
        "source_root",
        type=Path,
        metavar="SOURCE_ROOT",
        help="explicit import root (the directory normally placed on sys.path)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("build/pyarchgraph"),
        help="file output directory (default: build/pyarchgraph; unused for score only)",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="exclude a POSIX-relative file or directory glob; may be repeated",
    )
    parser.add_argument(
        "--view",
        choices=[view.value for view in View],
        default=View.MODULE.value,
        help=(
            "grain of the emitted DAG and diagram: 'module' (default) or "
            "'package', which projects modules onto a package prefix. The "
            "JSON always reports modules, facts and dependencies at module "
            "grain regardless of this setting"
        ),
    )
    parser.add_argument(
        "--package-depth",
        type=int,
        default=DEFAULT_PACKAGE_DEPTH,
        metavar="N",
        help=(
            "dotted segments to keep when --view package projects a module "
            f"(default: {DEFAULT_PACKAGE_DEPTH}). Depth 1 groups by top-level "
            "package; depth 2 usually gives one node per architectural area"
        ),
    )
    parser.add_argument(
        "--implied-edges",
        choices=[option.value for option in ImpliedEdges],
        default=ImpliedEdges.DOTTED.value,
        help=(
            "how the diagram draws an edge a longer path already implies: "
            "'dotted' (default) draws it dotted, 'solid' draws it like any "
            "other, 'omit' leaves it out. Omitting keeps reachability but "
            "understates coupling, because the implied edges on a layered "
            "codebase are often the heaviest ones. The JSON always lists "
            "every edge"
        ),
    )
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--output",
        choices=("json", "graph", "score"),
        action="append",
        help=(
            "select JSON file, Mermaid Markdown file, or score summary on stdout; "
            "repeat to combine (default: JSON and graph files, score on stderr)"
        ),
    )
    output_group.add_argument(
        "--json-only",
        action="store_true",
        help="alias for --output json; skips diagram rendering and transitive reduction",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        help="project directory used to record a portable relative source root",
    )
    parser.add_argument(
        "--expect-package",
        action="append",
        default=[],
        metavar="NAME",
        help="require this package/module in the inventory; repeatable",
    )
    parser.add_argument(
        "--include-tests",
        action="store_true",
        help="include tests directories, test_*.py and *_test.py (excluded by default)",
    )
    parser.add_argument(
        "--exclude-type-only",
        action="store_true",
        help="exclude typing-only evidence from the architecture graph",
    )
    parser.add_argument(
        "--exclude-local",
        action="store_true",
        help="exclude imports in function and class bodies from the architecture graph",
    )
    parser.add_argument(
        "--forbid",
        action="append",
        default=[],
        metavar="SOURCE:TARGET",
        help="forbid direct dependencies matching two module-name globs; repeatable",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 3 for definite violations, 4 when review is needed; never gate on score",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        help="compare against a compatible dependency-graph.json; requires JSON output",
    )
    parser.add_argument(
        "--allow-inventory-change",
        action="store_true",
        help="allow reviewed module additions/removals in a baseline comparison",
    )
    return parser


def _format_diagnostic(diagnostic: Diagnostic) -> str:
    location = diagnostic.path
    if location is not None and diagnostic.line is not None:
        location = f"{location}:{diagnostic.line}"
    if location is not None and diagnostic.column is not None:
        location = f"{location}:{diagnostic.column + 1}"

    prefix = "pyarchgraph"
    if location is not None:
        prefix = f"{prefix}: {location}"
    return (
        f"{prefix}: {diagnostic.severity.value}[{diagnostic.code}]: "
        f"{diagnostic.message}"
    )


def _stage_write(path: Path, content: str) -> Path:
    """Durably stage one artifact beside its final destination."""

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return temporary
    except BaseException:
        if temporary is not None:
            # Cleanup is best effort and must not replace the staging error.
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
        raise


def _write_outputs_atomically(
    outputs: Sequence[tuple[Path, str]],
) -> None:
    """Stage requested artifacts before atomically replacing their final files."""

    staged: list[tuple[Path, Path]] = []
    try:
        for path, content in outputs:
            staged.append((_stage_write(path, content), path))
        while staged:
            temporary, path = staged[0]
            os.replace(temporary, path)
            staged.pop(0)
    finally:
        for temporary, _ in staged:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    selected_outputs = set(args.output or ("json", "graph"))
    if args.json_only:
        selected_outputs = {"json"}
    output_paths = {
        kind: args.output_dir / filename
        for kind, filename in (
            ("json", "dependency-graph.json"),
            ("graph", "dependency-dag.md"),
        )
        if kind in selected_outputs
    }

    if not args.source_root.exists():
        parser.error(f"source root does not exist: {args.source_root}")
    if not args.source_root.is_dir():
        parser.error(f"source root is not a directory: {args.source_root}")
    if output_paths and args.output_dir.exists() and not args.output_dir.is_dir():
        parser.error(f"output path is not a directory: {args.output_dir}")
    if args.package_depth < MINIMUM_PACKAGE_DEPTH:
        parser.error(
            f"--package-depth must be >= {MINIMUM_PACKAGE_DEPTH}: {args.package_depth}"
        )
    forbidden = []
    for rule in args.forbid:
        source, separator, target = rule.partition(":")
        if not separator or not source or not target or ":" in target:
            parser.error(
                "--forbid requires SOURCE:TARGET with nonempty module-name globs"
            )
        forbidden.append((source, target))
    if args.allow_inventory_change and args.baseline is None:
        parser.error("--allow-inventory-change requires --baseline")
    if args.baseline is not None and "json" not in selected_outputs:
        parser.error("--baseline requires JSON output; add --output json")

    for output_path in output_paths.values():
        if output_path.exists() and not output_path.is_file():
            parser.error(f"output target is not a regular file: {output_path}")

    try:
        result = analyse(
            args.source_root,
            excludes=tuple(args.exclude),
            view=View(args.view),
            package_depth=args.package_depth,
            project_root=args.project_root,
            expected_packages=tuple(args.expect_package),
            policy=GraphPolicy(
                include_type_only=not args.exclude_type_only,
                include_local=not args.exclude_local,
                include_tests=args.include_tests,
            ),
            forbidden_dependencies=tuple(forbidden),
        )
        file_outputs: list[tuple[Path, str]] = []
        if "json" in selected_outputs:
            json_output = render_json(result)
            if args.baseline is not None:
                baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
                if not isinstance(baseline, dict):
                    raise ValueError("incompatible baseline: expected a JSON object")
                document = json.loads(json_output)
                document["baseline_comparison"] = compare_baseline(
                    document, baseline, allow_inventory_change=args.allow_inventory_change
                )
                json_output = (
                    json.dumps(
                        document,
                        sort_keys=True,
                        indent=2,
                        ensure_ascii=False,
                        allow_nan=False,
                    )
                    + "\n"
                )
            file_outputs.append((output_paths["json"], json_output))
        if "graph" in selected_outputs:
            mermaid_output = render_mermaid_markdown(
                result,
                implied_edges=ImpliedEdges(args.implied_edges),
            )
            file_outputs.append((output_paths["graph"], mermaid_output))
        score_summary = (
            f"{render_quality_summary(result.quality)}; "
            f"{result.quality.unresolved_import_count} unresolved import records, "
            f"{result.quality.dynamic_import_warning_count} dynamic-import warnings"
        )
        if file_outputs:
            args.output_dir.mkdir(parents=True, exist_ok=True)
            _write_outputs_atomically(file_outputs)
    except (OSError, ValueError) as exc:
        print(f"pyarchgraph: {exc}", file=sys.stderr)
        return 2

    for diagnostic in result.diagnostics:
        print(_format_diagnostic(diagnostic), file=sys.stderr)

    cyclic_count = result.quality.metrics.cyclic_component_count
    publication_summary = (
        "; wrote " + " and ".join(str(path) for path, _ in file_outputs)
        if file_outputs
        else ""
    )
    print(
        "pyarchgraph: "
        f"{len(result.modules)} modules, "
        f"{len(result.dependencies)} raw edges, "
        f"{cyclic_count} cyclic components, "
        f"{len(result.diagnostics)} diagnostics{publication_summary}",
        file=sys.stderr,
    )
    if "score" in selected_outputs:
        print(score_summary)
    else:
        print(f"pyarchgraph: {score_summary}", file=sys.stderr)
    status = check_status(result)
    print(f"pyarchgraph: policy check: {status}", file=sys.stderr)
    if not result.complete:
        return 1
    if args.check:
        return {"pass": 0, "fail": 3, "needs_review": 4}[status]
    return 0
