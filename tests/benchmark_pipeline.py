"""Manual full-CLI benchmark: uv run python tests/benchmark_pipeline.py.

The timed subprocess includes Python startup, discovery, extraction, ID
assignment, resolution, graph analysis and writing the JSON artifact. Fixture
generation and inspection of the resulting JSON are outside the timer. Add
--with-diagram to measure the default Mermaid rendering path as well.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from statistics import median
import subprocess
import sys
import tempfile
from time import perf_counter

from pyarchgraph.extraction import _minimum_unique_prefix_lengths


def _quadratic_prefix_reference(digests: tuple[str, ...]) -> tuple[int, ...]:
    """The reviewed implementation, retained only for opt-in comparisons."""

    lengths = []
    for index, digest in enumerate(digests):
        length = 12
        while any(
            other_index != index
            and other != digest
            and other.startswith(digest[:length])
            for other_index, other in enumerate(digests)
        ):
            length += 1
        lengths.append(length)
    return tuple(lengths)


def _prefix_timings(
    sizes: list[int], compare_quadratic: bool
) -> list[dict[str, object]]:
    timings = []
    for size in sizes:
        digests = tuple(
            hashlib.sha256(str(index).encode()).hexdigest() for index in range(size)
        )
        started = perf_counter()
        optimized = _minimum_unique_prefix_lengths(digests)
        result: dict[str, object] = {
            "digests": size,
            "sorted_neighbours_seconds": round(perf_counter() - started, 6),
        }
        if compare_quadratic:
            started = perf_counter()
            reference = _quadratic_prefix_reference(digests)
            result["quadratic_reference_seconds"] = round(perf_counter() - started, 6)
            if reference != optimized:
                raise RuntimeError("Prefix implementations produced different IDs")
        timings.append(result)
    return timings


def _write_fixture(root: Path, count: int, case: str) -> int:
    offsets = (1, 7, 31, 127, 511) if case == "layered" else (1,)
    import_count = 0
    for index in range(count):
        targets = [index - offset for offset in offsets if index >= offset]
        if case == "cycle" and index == 0:
            targets.append(count - 1)
        source = "".join(f"import module{target:05d}\n" for target in targets)
        (root / f"module{index:05d}.py").write_text(source, encoding="utf-8")
        import_count += len(targets)
    return import_count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case", choices=("chain", "layered", "cycle"), default="layered"
    )
    parser.add_argument("--modules", type=int, default=2000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--with-diagram", action="store_true")
    parser.add_argument(
        "--prefix-sizes", nargs="+", type=int, default=[1000, 2000, 4000, 8000]
    )
    parser.add_argument(
        "--compare-quadratic",
        action="store_true",
        help="also time the old prefix helper (can take tens of seconds at 8,000 hashes)",
    )
    args = parser.parse_args()
    if (
        args.modules < 1
        or args.repeats < 1
        or any(size < 1 for size in args.prefix_sizes)
    ):
        parser.error("module, repeat and digest counts must be positive")

    prefix_timings = _prefix_timings(args.prefix_sizes, args.compare_quadratic)
    elapsed_samples = []
    document: dict[str, object]
    with tempfile.TemporaryDirectory(prefix="pyarchgraph-benchmark-") as temporary:
        source = Path(temporary) / "source"
        source.mkdir()
        import_count = _write_fixture(source, args.modules, args.case)
        for repeat in range(args.repeats):
            output = Path(temporary) / f"output-{repeat}"
            command = [
                sys.executable,
                "-m",
                "pyarchgraph",
                str(source),
                "--output-dir",
                str(output),
            ]
            if not args.with_diagram:
                command.append("--json-only")
            started = perf_counter()
            completed = subprocess.run(
                command, capture_output=True, text=True, check=False
            )
            elapsed_samples.append(perf_counter() - started)
            if completed.returncode != 0:
                raise RuntimeError(
                    f"CLI failed with {completed.returncode}: {completed.stderr}"
                )
            document = json.loads(
                (output / "dependency-graph.json").read_text(encoding="utf-8")
            )
            if (
                len(document["modules"]) != args.modules
                or len(document["import_facts"]) != import_count
            ):
                raise RuntimeError(
                    "CLI output did not preserve the full fixture inventory"
                )
            if not args.with_diagram and (output / "dependency-dag.md").exists():
                raise RuntimeError("JSON-only CLI unexpectedly rendered a diagram")
            expected_debt = args.modules if args.case == "cycle" else 0
            if document["cleanup"]["violation_count"] != expected_debt:
                raise RuntimeError("Cleanup debt did not match the benchmark graph")

    try:
        import resource
    except ImportError:
        peak_mib = None
    else:
        peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        peak_mib = peak / (1024 * 1024 if sys.platform == "darwin" else 1024)
    print(
        json.dumps(
            {
                "case": args.case,
                "modules": args.modules,
                "imports": import_count,
                "dependencies": len(document["dependencies"]),
                "score": document["quality"]["score"],
                "cleanup_violation_count": document["cleanup"]["violation_count"],
                "cleanup_work_items": len(document["cleanup"]["work_items"]),
                "json_only": not args.with_diagram,
                "cli_seconds": [round(sample, 6) for sample in elapsed_samples],
                "median_cli_seconds": round(median(elapsed_samples), 6),
                "peak_cli_process_mib": round(peak_mib, 1)
                if peak_mib is not None
                else None,
                "prefix_timings": prefix_timings,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
