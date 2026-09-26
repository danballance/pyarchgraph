"""Manual isolated discovery benchmark, excluding filesystem I/O.

Run: uv run python tests/benchmark_discovery.py --compare-quadratic

Times the complete ambiguity-removal helper on unrelated modules. The optional
reference times only the old all-pairs prefix scan, a lower bound for the old
helper. Inventory construction and correctness checks are outside the timer.
No wall-clock limits are used in the test suite.
"""

from __future__ import annotations

import argparse
import json
from statistics import median
from time import perf_counter

from pyarchgraph.discovery import _Candidate, _remove_ambiguous_groups
from pyarchgraph.model import SourceModule


def _inventory(count: int, case: str) -> tuple[_Candidate, ...]:
    names = (
        f"module{index:06d}" if case == "flat" else f"namespace.group{index:06d}.leaf"
        for index in range(count)
    )
    return tuple(
        _Candidate(
            SourceModule(
                name,
                name.replace(".", "/") + ".py",
                False,
                name.rpartition(".")[0] or None,
            )
        )
        for name in names
    )


def _quadratic_prefix_scan(candidates: tuple[_Candidate, ...]) -> int:
    """The previous prefix search, without the rest of discovery's work."""

    by_id: dict[str, list[int]] = {}
    for index, candidate in enumerate(candidates):
        by_id.setdefault(candidate.module.id, []).append(index)
    conflicts = 0
    for prefix_id, prefix_indexes in sorted(by_id.items()):
        non_package_indexes = [
            index for index in prefix_indexes if not candidates[index].module.is_package
        ]
        if not non_package_indexes:
            continue
        descendant_indexes = [
            index
            for module_id, indexes in by_id.items()
            if module_id.startswith(prefix_id + ".")
            for index in indexes
        ]
        conflicts += len(descendant_indexes)
    return conflicts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sizes", nargs="+", type=int, default=[1000, 2000, 4000, 8000]
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--case", choices=("flat", "namespaces"), default="flat")
    parser.add_argument("--compare-quadratic", action="store_true")
    args = parser.parse_args()
    if args.repeats < 1 or any(size < 1 for size in args.sizes):
        parser.error("sizes and repeats must be positive")

    rows = []
    for size in args.sizes:
        candidates = _inventory(size, args.case)
        samples = []
        for _ in range(args.repeats):
            started = perf_counter()
            retained, diagnostics = _remove_ambiguous_groups(candidates)
            samples.append(perf_counter() - started)
            if retained != candidates or diagnostics:
                raise RuntimeError(
                    "Discovery changed the unambiguous fixture inventory"
                )
        row: dict[str, object] = {
            "modules": size,
            "helper_seconds": [round(sample, 6) for sample in samples],
            "median_helper_seconds": round(median(samples), 6),
        }
        if args.compare_quadratic:
            started = perf_counter()
            conflicts = _quadratic_prefix_scan(candidates)
            row["old_prefix_scan_seconds"] = round(perf_counter() - started, 6)
            if conflicts:
                raise RuntimeError("Reference found an unexpected fixture conflict")
        rows.append(row)

    print(json.dumps({"case": args.case, "timings": rows}, indent=2))


if __name__ == "__main__":
    main()
