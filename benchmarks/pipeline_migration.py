"""Compare schema-normalized 0.6 and 0.7 CLI reports and paired pipeline timing.

The baseline runtime is read from a pinned Git revision into a temporary
directory; fixture applications and archived data are never modified. Run:
python -m benchmarks.pipeline_migration --output /tmp/pipeline.json
Add --research-case sympy when the archived working directory is available.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from statistics import median

PROJECT = Path(__file__).resolve().parents[1]
BASELINE_REF = "caac3f23bd879cdf51fe78dae76edca06ec82d8a"


def normalized(report):
    if isinstance(report, list):
        return [normalized(item) for item in report]
    if not isinstance(report, dict):
        return report
    if set(report) == {"check_id", "severity", "finding"}:
        if report["severity"] != "error":
            raise AssertionError("default built-in finding severity changed")
        return normalized(report["finding"])
    result = {}
    for key, value in report.items():
        if key in {"schema_version", "enabled_check_ids", "nodes", "node", "fact_id"}:
            continue
        if key in {"source", "target"} and "source_segment" in report:
            continue
        key = {
            "non_typing": "non-typing",
            "module_body": "module-body",
            "cyclic_source_count": "cyclic_node_count",
        }.get(key, key)
        result[key] = normalized(value)
    return result


def worker(case):
    sys.path.insert(0, case["runtime"])
    if case["implementation"] == "baseline":
        from pyarchgraph.cli import main

        run = main
    else:
        from pyarchgraph import ApplicationFactory

        run = ApplicationFactory().create_cli().run
    os.chdir(PROJECT)
    results = []
    case_timings = {}
    case_hashes = {}
    for item in case["cases"]:
        os.chdir(item.get("cwd", PROJECT))
        stdout, stderr = io.StringIO(), io.StringIO()
        started, cpu_started = time.perf_counter(), time.process_time()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = run(item["arguments"])
        case_timings[item["name"]] = {
            "wall_seconds": time.perf_counter() - started,
            "cpu_seconds": time.process_time() - cpu_started,
        }
        result = {
            "name": item["name"],
            "exit_code": code,
            "stderr": stderr.getvalue(),
            "report": normalized(json.loads(stdout.getvalue())),
        }
        results.append(result)
        case_hashes[item["name"]] = hashlib.sha256(
            json.dumps(result, sort_keys=True).encode()
        ).hexdigest()
    return {
        "wall_seconds": sum(item["wall_seconds"] for item in case_timings.values()),
        "cpu_seconds": sum(item["cpu_seconds"] for item in case_timings.values()),
        "case_timings": case_timings,
        "case_semantic_sha256": case_hashes,
        "semantic_sha256": hashlib.sha256(
            json.dumps(results, sort_keys=True).encode()
        ).hexdigest(),
        "case_count": len(results),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--baseline-ref", default=BASELINE_REF)
    parser.add_argument("--research-case", action="append", default=[])
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(json.load(sys.stdin))))
        return 0
    if args.output is None or args.repeats < 1:
        parser.error("--output and a positive --repeats are required")
    manifest = json.loads((PROJECT / "examples/manifest.json").read_text())
    cases = []
    for project in manifest["projects"]:
        for variant in [project, *project.get("variants", [])]:
            directory = PROJECT / "examples/projects" / project["id"]
            arguments = [str(directory / root) for root in variant["source_roots"]]
            arguments.extend(
                ("--gate", variant["gate"], "--details", variant["details"])
            )
            for exclude in variant["exclusions"]:
                arguments.extend(("--exclude", exclude))
            if variant["config"]:
                arguments.extend(("--config", str(directory / variant["config"])))
            cases.append(
                {
                    "name": project["id"] + "/" + variant.get("id", "default"),
                    "arguments": arguments,
                }
            )
    for name in args.research_case:
        archive_path = (
            PROJECT / "docs/validation/2026-09-26" / (name + ".current.run.json")
        )
        archived = json.loads(archive_path.read_text())
        root = Path(archived["cwd"]) / archived["source_root"]
        if not root.is_dir():
            parser.error(f"archived source root unavailable: {root}")
        arguments = [str(root)]
        for exclude in archived["excludes"]:
            arguments.extend(("--exclude", exclude))
        cases.append(
            {"name": "research:" + name, "arguments": arguments, "cwd": archived["cwd"]}
        )
    baseline_commit = subprocess.check_output(
        ["git", "rev-parse", "--verify", args.baseline_ref + "^{commit}"],
        cwd=PROJECT,
        text=True,
    ).strip()
    current_hashes = {
        str(path.relative_to(PROJECT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (PROJECT / "pyarchgraph").rglob("*.py")
    }
    timings = {"baseline": [], "current": []}
    with tempfile.TemporaryDirectory(prefix="pyarchgraph-pipeline-") as folder:
        baseline = Path(folder) / "baseline"
        paths = subprocess.check_output(
            ["git", "ls-tree", "-r", "--name-only", baseline_commit, "pyarchgraph"],
            cwd=PROJECT,
            text=True,
        ).splitlines()
        for relative in paths:
            path = baseline / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(
                subprocess.check_output(
                    ["git", "show", baseline_commit + ":" + relative], cwd=PROJECT
                )
            )
        synthetic = Path(folder) / "synthetic"
        synthetic.mkdir()
        for group in range(30):
            names = [f"group_{group}_module_{index}" for index in range(8)]
            for name in names:
                (synthetic / (name + ".py")).write_text(
                    "".join(f"import {other}\n" for other in names if other != name)
                )
        cases.append(
            {"name": "synthetic-30-dense-components", "arguments": [str(synthetic)]}
        )
        reference = None
        reference_cases = None
        for iteration in range(args.repeats):
            order = (
                ("baseline", "current")
                if iteration % 2 == 0
                else ("current", "baseline")
            )
            for implementation in order:
                request = {
                    "runtime": str(
                        baseline if implementation == "baseline" else PROJECT
                    ),
                    "implementation": implementation,
                    "cases": cases,
                }
                result = subprocess.run(
                    [sys.executable, str(Path(__file__)), "--worker"],
                    input=json.dumps(request),
                    capture_output=True,
                    text=True,
                    check=True,
                )
                observation = json.loads(result.stdout)
                if (
                    reference is not None
                    and observation["semantic_sha256"] != reference
                ):
                    raise AssertionError(
                        "normalized 0.6/0.7 reports or exit codes differ: "
                        + ", ".join(
                            name
                            for name, digest in observation[
                                "case_semantic_sha256"
                            ].items()
                            if reference_cases.get(name) != digest
                        )
                    )
                reference = observation["semantic_sha256"]
                reference_cases = observation["case_semantic_sha256"]
                timings[implementation].append(observation)
                print(
                    json.dumps(
                        {
                            "iteration": iteration + 1,
                            "implementation": implementation,
                            **observation,
                        }
                    ),
                    flush=True,
                )
    if any(
        hashlib.sha256((PROJECT / path).read_bytes()).hexdigest() != digest
        for path, digest in current_hashes.items()
    ):
        raise AssertionError("current runtime sources changed during benchmark")
    medians = {
        name: {
            metric: median(item[metric] for item in observations)
            for metric in ("wall_seconds", "cpu_seconds")
        }
        for name, observations in timings.items()
    }
    report = {
        "python": sys.version,
        "baseline_commit": baseline_commit,
        "baseline_ref": args.baseline_ref,
        "research_cases": args.research_case,
        "current_source_sha256": current_hashes,
        "current_sources_unchanged_during_run": True,
        "repeats": args.repeats,
        "case_count": len(cases),
        "schema_normalized_reports_exit_codes_equal": True,
        "seconds": timings,
        "median_seconds": medians,
        "current_over_baseline_ratio": {
            metric: medians["current"][metric] / medians["baseline"][metric]
            for metric in ("wall_seconds", "cpu_seconds")
        },
        "scope": "40 committed corpus runs, 240-module synthetic graph and optional archived research cases; includes real CLI parsing, discovery, extraction, resolution, graph strategies and rendering; process startup and schema normalization excluded",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
