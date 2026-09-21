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
            "Statically analyse internal Python imports and emit a raw graph "
            "plus an SCC-condensed DAG."
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
        help="output directory (default: build/pyarchgraph)",
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
    parser.add_argument(
        "--json-only",
        action="store_true",
        help="write evidence JSON without diagram rendering or transitive reduction",
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
        help="compare against a compatible dependency-graph.json",
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
    json_path: Path,
    json_output: str,
    mermaid_path: Path | None,
    mermaid_output: str | None,
) -> None:
    """Stage requested artifacts before atomically replacing their final files."""

    staged: list[Path] = []
    try:
        staged.append(_stage_write(json_path, json_output))
        if mermaid_path is not None and mermaid_output is not None:
            staged.append(_stage_write(mermaid_path, mermaid_output))
        os.replace(staged[0], json_path)
        staged.pop(0)
        if staged and mermaid_path is not None:
            os.replace(staged[0], mermaid_path)
            staged.pop(0)
    finally:
        for temporary in staged:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    if not args.source_root.exists():
        parser.error(f"source root does not exist: {args.source_root}")
    if not args.source_root.is_dir():
        parser.error(f"source root is not a directory: {args.source_root}")
    if args.output_dir.exists() and not args.output_dir.is_dir():
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

    json_path = args.output_dir / "dependency-graph.json"
    mermaid_path = None if args.json_only else args.output_dir / "dependency-dag.md"
    for output_path in (json_path, mermaid_path):
        if (
            output_path is not None
            and output_path.exists()
            and not output_path.is_file()
        ):
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
        mermaid_output = (
            None
            if args.json_only
            else render_mermaid_markdown(
                result,
                implied_edges=ImpliedEdges(args.implied_edges),
            )
        )
        args.output_dir.mkdir(parents=True, exist_ok=True)
        _write_outputs_atomically(
            json_path,
            json_output,
            mermaid_path,
            mermaid_output,
        )
    except (OSError, ValueError) as exc:
        print(f"pyarchgraph: {exc}", file=sys.stderr)
        return 2

    for diagnostic in result.diagnostics:
        print(_format_diagnostic(diagnostic), file=sys.stderr)

    cyclic_count = result.quality.metrics.cyclic_component_count
    print(
        "pyarchgraph: "
        f"{len(result.modules)} modules, "
        f"{len(result.dependencies)} raw edges, "
        f"{cyclic_count} cyclic components, "
        f"{len(result.diagnostics)} diagnostics; "
        f"wrote {json_path}" + (f" and {mermaid_path}" if mermaid_path else ""),
        file=sys.stderr,
    )
    print(
        f"pyarchgraph: {render_quality_summary(result.quality)}; "
        f"{result.quality.unresolved_import_count} unresolved import records, "
        f"{result.quality.dynamic_import_warning_count} dynamic-import warnings",
        file=sys.stderr,
    )
    status = check_status(result)
    print(f"pyarchgraph: policy check: {status}", file=sys.stderr)
    if not result.complete:
        return 1
    if args.check:
        return {"pass": 0, "fail": 3, "needs_review": 4}[status]
    return 0
