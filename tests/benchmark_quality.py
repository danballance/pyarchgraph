"""Manual benchmark: uv run python tests/benchmark_quality.py --case layered."""

from __future__ import annotations

import argparse
import json
import sys
from time import perf_counter

from pyarchgraph.model import DependencyEdge, SourceModule
from pyarchgraph.quality import calculate_quality


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case", choices=("chain", "layered", "cycle"), default="layered"
    )
    parser.add_argument("--modules", type=int, default=10000)
    args = parser.parse_args()
    if args.modules < 1:
        parser.error("--modules must be positive")

    names = tuple(f"module{index:05d}" for index in range(args.modules))
    modules = tuple(SourceModule(name, f"{name}.py", False, None) for name in names)
    offsets = (1, 7, 31, 127, 511) if args.case == "layered" else (1,)
    pairs = [
        (i, i - offset)
        for i in range(args.modules)
        for offset in offsets
        if i >= offset
    ]
    if args.case == "cycle":
        pairs.append((0, args.modules - 1))
    dependencies = tuple(DependencyEdge(names[a], names[b], ()) for a, b in pairs)
    started = perf_counter()
    quality = calculate_quality(modules, dependencies, complete=True)
    elapsed = perf_counter() - started

    try:
        import resource
    except ImportError:
        peak_mib = None
    else:
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_mib = peak / (1024 * 1024 if sys.platform == "darwin" else 1024)
    print(
        json.dumps(
            {
                "case": args.case,
                "modules": args.modules,
                "dependencies": quality.metrics.dependency_count,
                "reachable_pairs": quality.metrics.reachable_pair_count,
                "score": quality.score,
                "scoring_seconds": round(elapsed, 3),
                "peak_process_mib": round(peak_mib, 1)
                if peak_mib is not None
                else None,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
