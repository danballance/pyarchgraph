"""Measure context traversal changes against the retained pre-change collector."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from dataclasses import asdict
from pathlib import Path
from statistics import median
from time import perf_counter

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from benchmarks.reference_extraction import AstImportFactSource  # noqa: E402
from pyarchgraph.adapters.driven.filesystem.discovery import (
    FileSystemSourceDiscovery,  # noqa: E402
)
from pyarchgraph.domain.canonicalization import FactCanonicalizer  # noqa: E402
from pyarchgraph.main import ApplicationFactory  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be positive")
    record_path = PROJECT / "docs/validation/2026-09-26/sympy.current.run.json"
    record = json.loads(record_path.read_text())
    root = Path(record["cwd"]) / record["source_root"]
    excludes = tuple(record["excludes"]) + ("tests", "test_*.py", "*_test.py")
    inventory = FileSystemSourceDiscovery().discover(root, excludes=excludes)
    if inventory.diagnostics:
        raise ValueError(
            f"Unexpected SymPy inventory diagnostics: {inventory.diagnostics}"
        )
    collectors = {
        "reference": AstImportFactSource(),
        "optimized": ApplicationFactory().create_fact_source(),
    }
    timings = {name: [] for name in collectors}
    previous = None
    for iteration in range(args.repeats):
        names = tuple(collectors) if iteration % 2 == 0 else tuple(reversed(collectors))
        for name in names:
            start = perf_counter()
            result = collectors[name].collect(root, inventory.modules)
            # Both measurements include final fact IDs. The runtime collector
            # now returns drafts, and canonicalization belongs to its caller.
            facts = (
                FactCanonicalizer().canonicalise(result.facts)
                if name == "optimized"
                else result.facts
            )
            elapsed = perf_counter() - start
            timings[name].append(elapsed)
            observation = {
                "facts": tuple(asdict(fact) for fact in facts),
                "diagnostics": tuple(asdict(item) for item in result.diagnostics),
            }
            if previous is not None and observation != previous:
                raise AssertionError(
                    "Collector facts, IDs, contexts or diagnostics differ"
                )
            previous = observation
            print(
                json.dumps(
                    {"iteration": iteration + 1, "collector": name, "seconds": elapsed}
                ),
                flush=True,
            )
    assert previous is not None
    result_hash = hashlib.sha256(
        json.dumps(previous, sort_keys=True).encode()
    ).hexdigest()
    report = {
        "python": platform.python_version(),
        "repeats": args.repeats,
        "source_root": str(root),
        "excludes": excludes,
        "source_module_count": len(inventory.modules),
        "fact_count": len(previous["facts"]),
        "facts_ids_contexts_diagnostics_equal": True,
        "collection_sha256": result_hash,
        "seconds": timings,
        "median_seconds": {name: median(values) for name, values in timings.items()},
        "median_speedup": median(timings["reference"]) / median(timings["optimized"]),
        "reference_source_sha256": hashlib.sha256(
            (PROJECT / "benchmarks/reference_extraction.py").read_bytes()
        ).hexdigest(),
        "reference_models_sha256": hashlib.sha256(
            (PROJECT / "benchmarks/reference_models.py").read_bytes()
        ).hexdigest(),
        "optimized_source_sha256": hashlib.sha256(
            (PROJECT / "pyarchgraph/adapters/driven/python_ast.py").read_bytes()
        ).hexdigest(),
        "scope": "Repeated extraction from existing archived SymPy working directory; target source parsed, never executed. Includes context classification and fact IDs, excludes discovery/resolution/rendering.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
