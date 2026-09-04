"""Command-line interface for pyarchgraph."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from pyarchgraph.analysis import analyse
from pyarchgraph.model import Diagnostic
from pyarchgraph.rendering import render_json, render_mermaid_markdown


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
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        return temporary
    except BaseException:
        if "temporary" in locals():
            temporary.unlink(missing_ok=True)
        raise


def _write_outputs_atomically(
    json_path: Path,
    json_output: str,
    mermaid_path: Path,
    mermaid_output: str,
) -> None:
    """Stage both artifacts before atomically replacing either final file."""

    staged: list[Path] = []
    try:
        staged.append(_stage_write(json_path, json_output))
        staged.append(_stage_write(mermaid_path, mermaid_output))
        os.replace(staged[0], json_path)
        staged.pop(0)
        os.replace(staged[0], mermaid_path)
        staged.pop(0)
    finally:
        for temporary in staged:
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

    json_path = args.output_dir / "dependency-graph.json"
    mermaid_path = args.output_dir / "dependency-dag.md"
    for output_path in (json_path, mermaid_path):
        if output_path.exists() and not output_path.is_file():
            parser.error(f"output target is not a regular file: {output_path}")

    try:
        result = analyse(
            args.source_root,
            excludes=tuple(args.exclude),
        )
        json_output = render_json(result)
        mermaid_output = render_mermaid_markdown(result)
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

    cyclic_count = sum(node.cyclic for node in result.dag.nodes)
    print(
        "pyarchgraph: "
        f"{len(result.modules)} modules, "
        f"{len(result.dependencies)} raw edges, "
        f"{cyclic_count} cyclic components, "
        f"{len(result.diagnostics)} diagnostics; "
        f"wrote {json_path} and {mermaid_path}",
        file=sys.stderr,
    )
    return 0 if result.complete else 1
