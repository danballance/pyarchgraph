"""Compare cached collection with repeated ast.get_source_segment extraction.

Run from the project root with Python's installed pyarchgraph environment:
python benchmarks/collection.py --output benchmarks/collection-results.json
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import platform
from pathlib import Path
from statistics import median
from time import perf_counter

from pyarchgraph.adapters.driven.python_ast import ModuleImportExtractor
from pyarchgraph.domain.canonicalization import FactCanonicalizer
from pyarchgraph.domain.models import ImportFactDraft, ImportSyntax, SourceModule


def reference_facts(module: SourceModule, source: str, tree: ast.Module):
    """Keep current context semantics but repeat the former snippet work."""
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for node, context in ModuleImportExtractor()._imports_with_context(module, tree):
        is_from = isinstance(node, ast.ImportFrom)
        for alias_index, alias in enumerate(node.names):
            yield ImportFactDraft(
                source=module.id,
                path=module.path,
                line=node.lineno,
                column=len(lines[node.lineno - 1].encode()[: node.col_offset].decode()),
                end_line=node.end_lineno,
                end_column=(
                    len(
                        lines[node.end_lineno - 1]
                        .encode()[: node.end_col_offset]
                        .decode()
                    )
                    if node.end_lineno is not None and node.end_col_offset is not None
                    else None
                ),
                alias_index=alias_index,
                syntax=ImportSyntax.IMPORT_FROM if is_from else ImportSyntax.IMPORT,
                source_segment=ast.get_source_segment(source, node),
                base_module=node.module if is_from else alias.name,
                imported_name=alias.name if is_from else None,
                as_name=alias.asname,
                bound_name=alias.asname
                or (alias.name if is_from else alias.name.partition(".")[0]),
                relative_level=node.level if is_from else 0,
                context=context,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--statements", type=int, default=1000)
    parser.add_argument("--aliases", type=int, default=8)
    args = parser.parse_args()
    if min(args.repeats, args.statements, args.aliases) < 1:
        parser.error("repeats, statements and aliases must be positive")
    source = (
        "\n".join(
            f"from package_{index} import "
            + ", ".join(f"name_{alias}" for alias in range(args.aliases))
            for index in range(args.statements)
        )
        + "\n"
    )
    module = SourceModule("source:synthetic.py", "synthetic.py", False, None)
    functions = {
        "reference": reference_facts,
        "cached": ModuleImportExtractor().extract,
    }
    timings: dict[str, list[float]] = {name: [] for name in functions}
    previous = None
    for iteration in range(args.repeats):
        # Alternate order to reduce systematic warm-cache/order bias.
        names = tuple(functions) if iteration % 2 == 0 else tuple(reversed(functions))
        for name in names:
            start = perf_counter()
            tree = ast.parse(source)
            result = FactCanonicalizer().canonicalise(
                functions[name](module, source, tree)
            )
            timings[name].append(perf_counter() - start)
            if previous is not None and result != previous:
                raise AssertionError(
                    "optimized facts differ from reference, including IDs/context"
                )
            previous = result
    report = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "statement_count": args.statements,
        "aliases_per_statement": args.aliases,
        "fact_count": len(previous or ()),
        "repeats": args.repeats,
        "facts_and_ids_equal": True,
        "scope": "AST parse, context collection, snippet extraction and canonical fact IDs; synthetic LF source",
        "reference": "Current context semantics with ast.get_source_segment repeated once per alias",
        "seconds": timings,
        "median_seconds": {name: median(values) for name, values in timings.items()},
        "median_speedup": median(timings["reference"]) / median(timings["cached"]),
        "collector_source_sha256": hashlib.sha256(
            (
                Path(__file__).resolve().parents[1]
                / "pyarchgraph/adapters/driven/python_ast.py"
            ).read_bytes()
        ).hexdigest(),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
