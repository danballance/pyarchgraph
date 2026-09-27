"""Replay frozen research scopes and audit fresh schema 0.7 JSON artifacts.

Target code is parsed, never imported, installed or executed. Archived research
files are read-only. The worker invokes the real CLI and observes its graph
input for supplementary comparisons; that graph is not an independent oracle.
"""

from __future__ import annotations

import argparse
import ast
import cProfile
import hashlib
import io
import json
import os
import pstats
import subprocess
import sys
import tempfile
import time
import tokenize
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
ARCHIVE = PROJECT / "docs/validation/2026-09-26"
OUTPUT = PROJECT / "docs/validation/0.7"


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def archival_hashes():
    files = [ARCHIVE.with_suffix(".md"), *sorted(ARCHIVE.rglob("*"))]
    return {
        path.relative_to(PROJECT).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in files
        if path.is_file()
    }


def analyzer_hashes():
    return {
        path.relative_to(PROJECT).as_posix(): hashlib.sha256(
            path.read_bytes()
        ).hexdigest()
        for path in sorted((PROJECT / "pyarchgraph").rglob("*.py"))
    }


def source_labels(report):
    return {
        source["id"]: source["import_name"]
        if source["import_name"] and source["binding_status"] == "bound"
        else "path:" + source["path"]
        for source in report["sources"]
    }


def worker(case):
    hashes_before = analyzer_hashes()
    sys.path.insert(0, str(PROJECT))
    from pyarchgraph.application.strategies import StrategyEngine
    from pyarchgraph.composition import ApplicationFactory

    graphs = {}
    self_evidence = []
    original = StrategyEngine.evaluate

    def capture(self, snapshot, *, details="summary"):
        dependencies, facts = snapshot.dependencies, snapshot.facts
        by_id = {fact.id: fact for fact in facts}
        filters = {
            "structural": lambda fact: True,
            "non-typing": lambda fact: not fact.context.typing_only,
            "module-body": lambda fact: (
                not fact.context.typing_only and not fact.context.in_function
            ),
        }
        for name, eligible in filters.items():
            graphs[name] = sorted(
                [edge.source, edge.target]
                for edge in dependencies
                if any(eligible(by_id[item.fact_id]) for item in edge.evidence)
            )
        for edge in dependencies:
            if edge.source == edge.target:
                self_evidence.append(
                    {
                        "source": edge.source,
                        "evidence": [
                            {
                                "path": by_id[item.fact_id].path,
                                "line": by_id[item.fact_id].line,
                                "column": by_id[item.fact_id].column + 1,
                                "source_segment": by_id[item.fact_id].source_segment,
                                "context": asdict(by_id[item.fact_id].context),
                            }
                            for item in edge.evidence
                        ],
                    }
                )
        return original(self, snapshot, details=details)

    StrategyEngine.evaluate = capture
    os.chdir(case["cwd"])
    arguments = list(case["roots"])
    for pattern in case["excludes"]:
        arguments.extend(("--exclude", pattern))
    stdout, stderr = io.StringIO(), io.StringIO()
    profile = cProfile.Profile() if case.get("profile") else None
    with tempfile.TemporaryDirectory(prefix="pyarchgraph-replay-") as temporary:
        if case.get("targets"):
            config = Path(temporary) / "targets.toml"
            records = []
            for target in case["targets"]:
                records.append(
                    "[[targets]]\n"
                    + "\n".join(
                        f"{key} = {str(value).lower() if isinstance(value, bool) else json.dumps(value)}"
                        for key, value in target.items()
                        if value is not None
                    )
                )
            config.write_text("\n\n".join(records) + "\n")
            arguments.extend(("--config", str(config)))
        start = time.perf_counter()
        if profile is not None:
            profile.enable()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = ApplicationFactory().create_cli().run(arguments)
        if profile is not None:
            profile.disable()
        elapsed = time.perf_counter() - start
    StrategyEngine.evaluate = original
    payload = stdout.getvalue()
    report = json.loads(payload) if payload else None
    if report:
        names = source_labels(report)
        graphs = {
            name: [[names[source], names[target]] for source, target in edges]
            for name, edges in graphs.items()
        }
        self_evidence = [
            {**item, "source": names[item["source"]]} for item in self_evidence
        ]
    result = {
        "exit_code": exit_code,
        "elapsed_seconds": elapsed,
        "stderr": stderr.getvalue(),
        "report": report,
        "graphs": graphs,
        "self_edges": self_evidence,
        "analyzer_source_sha256": hashes_before,
        "analyzer_source_unchanged_during_run": hashes_before == analyzer_hashes(),
    }
    if profile is not None:
        stats = pstats.Stats(profile)
        functions = [
            {
                "file": filename,
                "line": line,
                "function": function,
                "primitive_calls": data[0],
                "total_calls": data[1],
                "self_seconds": data[2],
                "cumulative_seconds": data[3],
            }
            for (filename, line, function), data in stats.stats.items()
        ]
        result["profile"] = {
            "total_seconds": stats.total_tt,
            "total_calls": stats.total_calls,
            "primitive_calls": stats.prim_calls,
            "top_cumulative": sorted(
                functions, key=lambda item: item["cumulative_seconds"], reverse=True
            )[:30],
            "extraction_functions": [
                item
                for item in functions
                if item["file"].endswith("pyarchgraph/adapters/extraction.py")
            ],
            "scope": "One instrumented current SymPy CLI invocation; cumulative timings overlap and include profiler overhead.",
        }
    return result


def components(pairs):
    """Independent iterative Kosaraju traversal, without NetworkX."""
    forward, reverse = {}, {}
    for source, target in pairs:
        forward.setdefault(source, set()).add(target)
        forward.setdefault(target, set())
        reverse.setdefault(target, set()).add(source)
        reverse.setdefault(source, set())
    visited, order = set(), []
    for start in sorted(forward):
        if start in visited:
            continue
        visited.add(start)
        pending = [(start, iter(sorted(forward[start])))]
        while pending:
            node, children = pending[-1]
            child = next(children, None)
            if child is None:
                order.append(node)
                pending.pop()
            elif child not in visited:
                visited.add(child)
                pending.append((child, iter(sorted(forward[child]))))
    seen, groups = set(), []
    for start in reversed(order):
        if start in seen:
            continue
        group, pending = set(), [start]
        while pending:
            node = pending.pop()
            if node in group:
                continue
            group.add(node)
            pending.extend(reverse[node] - group - seen)
        seen.update(group)
        if len(group) > 1 or start in forward[start]:
            groups.append(sorted(group))
    return sorted(groups)


def source_sites(path):
    with tokenize.open(path) as stream:
        source = stream.read()
    tree = ast.parse(source, filename=str(path))
    lines = source.split("\n")
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    sites = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        column = len(lines[node.lineno - 1].encode()[: node.col_offset].decode()) + 1
        scope, function, handler = "module", False, False
        cursor = node
        while cursor in parents:
            cursor = parents[cursor]
            if isinstance(cursor, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function = True
                if scope == "module":
                    scope = "function"
            elif isinstance(cursor, ast.ClassDef) and scope == "module":
                scope = "class"
            elif isinstance(cursor, ast.ExceptHandler):
                handler = True
        sites[node.lineno, column] = {
            "source_segment": ast.get_source_segment(source, node),
            "scope": scope,
            "in_function": function,
            "exception_handler": handler,
        }
    return sites


def audit_report(case, result):
    report = result["report"]
    errors, checked = [], 0
    if report is None:
        return {
            "errors": ["CLI returned no report: " + result["stderr"]],
            "evidence_checked": 0,
        }
    names = source_labels(report)
    sources = {source["id"]: source for source in report["sources"]}
    gate = report["gate"]
    expected_exit = (
        2
        if report["status"] == "incomplete"
        else int(
            any(
                item["severity"] == "error"
                for item in report["views"][gate]["findings"]
            )
        )
    )
    if expected_exit != result["exit_code"]:
        errors.append("exit/report mismatch")
    if result["stderr"]:
        errors.append("unexpected stderr accompanying report")
    cache = {}

    def evidence_list(evidence, source_id=None, view_name="structural"):
        nonlocal checked
        if not evidence:
            errors.append("empty evidence list")
        encoded = [json.dumps(item, sort_keys=True) for item in evidence]
        if len(encoded) != len(set(encoded)):
            errors.append("duplicate displayed evidence")
        for item in evidence:
            checked += 1
            if source_id is not None and item["path"] != sources[source_id]["path"]:
                errors.append("evidence path does not belong to dependency source")
            if view_name != "structural" and item["context"]["typing_only"]:
                errors.append(f"{view_name}: typing-only evidence survived filter")
            if view_name == "module-body" and item["context"]["in_function"]:
                errors.append("module_body: deferred evidence survived filter")
            if item["context"]["package_initializer"] != (
                Path(item["path"]).name == "__init__.py"
            ):
                errors.append("incorrect package initializer context")
            path = Path(case["cwd"]) / item["path"]
            try:
                if path not in cache:
                    cache[path] = source_sites(path)
                expected = cache[path].get((item["line"], item["column"]))
                if (
                    expected is None
                    or expected["source_segment"] != item["source_segment"]
                ):
                    errors.append(
                        f"invalid evidence {path}:{item['line']}:{item['column']}"
                    )
                elif any(
                    expected[field] != item["context"][field]
                    for field in ("scope", "in_function", "exception_handler")
                ):
                    errors.append(f"invalid lexical context {path}:{item['line']}")
            except (OSError, SyntaxError, UnicodeError) as error:
                errors.append(f"evidence inspection failed {path}: {error}")

    for view_name, view in report["views"].items():
        pairs = result["graphs"][view_name]
        pair_set = {tuple(pair) for pair in pairs}
        if len(pair_set) != len(pairs):
            errors.append(f"{view_name}: duplicate graph edges")
        expected_components = components(pairs)
        cycles = [
            item["finding"]
            for item in view["findings"]
            if item["finding"]["kind"] == "cycle"
        ]
        actual_components = sorted(
            sorted(names[member] for member in finding["members"]) for finding in cycles
        )
        if actual_components != expected_components:
            errors.append(
                f"{view_name}: SCC memberships disagree with independent traversal"
            )
        if len(pairs) != view["dependency_count"]:
            errors.append(f"{view_name}: dependency count mismatch")
        if sum(map(len, actual_components)) != view["cyclic_node_count"]:
            errors.append(f"{view_name}: cyclic source count mismatch")
        cyclic_edges = sum(
            source in group and target in group
            for group in map(set, actual_components)
            for source, target in pairs
        )
        if cyclic_edges != view["cyclic_dependency_count"]:
            errors.append(f"{view_name}: cyclic dependency count mismatch")
        for registered in view["findings"]:
            finding = registered["finding"]
            if finding["kind"] != "cycle":
                evidence_list(finding["evidence"], finding["source"], view_name)
                continue
            members = set(finding["members"])
            definite_members = set(finding["definite_members"])
            if not definite_members <= members:
                errors.append(f"{view_name}: definite members exceed component")
            if bool(definite_members) != (finding["certainty"] == "definite"):
                errors.append(f"{view_name}: certainty disagrees with definite members")
            member_names = {names[member] for member in members}
            internal_pairs = {
                (source, target)
                for source, target in pair_set
                if source in member_names and target in member_names
            }
            if len(internal_pairs) != finding["dependency_count"]:
                errors.append(f"{view_name}: component dependency count mismatch")
            witness = finding["witness"]
            if not witness or len({edge["source"] for edge in witness}) != len(witness):
                errors.append(f"{view_name}: witness is empty or repeats source")
            for index, edge in enumerate(witness):
                if (
                    edge["source"] not in members
                    or edge["target"] != witness[(index + 1) % len(witness)]["source"]
                ):
                    errors.append(
                        f"{view_name}: witness is not closed within component"
                    )
                if (names[edge["source"]], names[edge["target"]]) not in pair_set:
                    errors.append(f"{view_name}: witness edge absent from graph")
                if (
                    finding["certainty"] == "definite"
                    and not {edge["source"], edge["target"]} <= definite_members
                ):
                    errors.append(
                        f"{view_name}: definite witness exceeds definite members"
                    )
                evidence_list(edge["evidence"], edge["source"], view_name)
                if finding["certainty"] == "definite" and any(
                    item["resolution_kind"] not in ("exact_base", "exact_module")
                    for item in edge["evidence"]
                ):
                    errors.append(
                        f"{view_name}: definite witness has uncertain evidence"
                    )
            if finding["dependencies"] is not None:
                displayed_pairs = {
                    (names[edge["source"]], names[edge["target"]])
                    for edge in finding["dependencies"]
                }
                if displayed_pairs != internal_pairs:
                    errors.append(
                        f"{view_name}: component detail does not cover its graph edges"
                    )
                for edge in finding["dependencies"]:
                    evidence_list(edge["evidence"], edge["source"], view_name)
        for boundary in (
            report["coverage"]["boundaries"] if view_name == "structural" else ()
        ):
            evidence_list(boundary["evidence"])
    independent_path = ARCHIVE / f"{case['original_case']}.independent.json"
    missing = []
    if independent_path.exists() and not case.get("supplement"):
        independent = json.loads(independent_path.read_text())
        actual = {tuple(pair) for pair in result["graphs"]["structural"]}
        missing = [
            pair
            for item in independent["edges"]
            if (pair := [item["source"], item["target"]]) and tuple(pair) not in actual
        ]
    return {
        "errors": errors,
        "evidence_checked": checked,
        "missing_restricted_independent_edges": missing,
        "independent_edge_limit": "Saved AST oracle covers restricted exact relations, not full precision/recall.",
    }


def targets_for(original_case, *, acknowledged):
    old = json.loads((ARCHIVE / f"{original_case}.current.stdout.json").read_text())
    record = json.loads((ARCHIVE / f"{original_case}.current.run.json").read_text())
    root = Path(record["cwd"])
    declarations = []
    native = {
        "pandas._libs._ujson": "pandas/_libs/src/vendored/ujson/python/ujson.c",
        "pandas._libs.pandas_datetime": "pandas/_libs/src/datetime/pd_datetime.c",
        "pandas._libs.pandas_parser": "pandas/_libs/src/parser/pd_parser.c",
    }
    names = {
        item["requested"]
        for item in old["findings"]
        if item["kind"] == "unresolved_import"
    }
    if original_case == "pandas":
        # Newly recognized package-child boundaries, verified in the pinned
        # Meson source map as well as their same-path Cython implementations.
        names.update({"pandas._libs.index", "pandas._libs.tslib"})
    for name in sorted(names):
        base = root / name.replace(".", "/")
        if name == "pandas._version_meson":
            kind, path = "generated", base.with_suffix(".py")
            reason = "Archived pandas-review.md: root meson.build:70-82 generates and installs this version module."
        elif name in native:
            kind, path = "native", root / native[name]
            reason = "Archived pandas-review.md and pandas/_libs/meson.build:132-164 explicitly define this C extension."
        elif base.with_suffix(".pyx").is_file():
            kind, path = "native", base.with_suffix(".pyx")
            reason = "Archived pandas-review.md target mapping; same-path Cython implementation exists in the pinned snapshot."
            if name in {"pandas._libs.index", "pandas._libs.tslib"}:
                reason = "Fresh pinned-source review: pandas/_libs/meson.build explicitly lists this same-path Cython source; package-child imports now expose this boundary."
        elif original_case.startswith("comfyui") and base.with_suffix(".pyi").is_file():
            kind, path = "stub", base.with_suffix(".pyi")
            reason = "Archived ComfyUI review maps this TYPE_CHECKING-only target to the tracked typing stub."
        else:
            raise ValueError(f"No recorded source/build support for boundary {name}")
        if kind != "generated" and not path.is_file():
            raise ValueError(f"Mapped target file is absent: {path}")
        declarations.append(
            {
                "name": name,
                "kind": kind,
                "path": str(path),
                "reason": reason,
                "acknowledged": acknowledged,
            }
        )
    return declarations


def record_passed(record):
    audit = record.get("audit")
    return (
        "exception" not in record
        and audit is not None
        and not audit["errors"]
        and not audit.get("missing_restricted_independent_edges", [])
        and record.get("analyzer_source_unchanged_during_run") is True
    )


def boundary_declarations_match(case, report):
    def identities(targets):
        return Counter(
            (
                target["name"],
                target["kind"],
                str((Path(case["cwd"]) / target["path"]).resolve()),
            )
            for target in targets
        )

    declared = case.get("targets", [])
    reported = report["coverage"]["boundaries"]
    return (
        report["status"] == "complete"
        and bool(declared)
        and all(target["acknowledged"] for target in declared + reported)
        and identities(declared) == identities(reported)
    )


def replay_case(case, output):
    # A failed replay must not leave a previous successful payload available to
    # expected-delta checks or readers of the output directory.
    for suffix in ("report.json", "graph.json", "profile.json", "run.json"):
        (output / f"{case['name']}.{suffix}").unlink(missing_ok=True)
    command = [sys.executable, "-I", str(Path(__file__).resolve()), "--worker"]
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            input=json.dumps(case),
            text=True,
            capture_output=True,
            cwd=PROJECT,
            timeout=90,
            check=False,
        )
        if completed.returncode:
            raise RuntimeError(
                f"worker exited {completed.returncode}: {completed.stderr}"
            )
        result = json.loads(completed.stdout)
        audit = audit_report(case, result)
        record = {
            "case": case,
            "exit_code": result["exit_code"],
            "stderr": result["stderr"],
            "elapsed_seconds": result["elapsed_seconds"],
            "audit": audit,
            "analyzer_source_sha256": result["analyzer_source_sha256"],
            "analyzer_source_unchanged_during_run": result[
                "analyzer_source_unchanged_during_run"
            ],
        }
        write_json(output / f"{case['name']}.report.json", result["report"])
        write_json(
            output / f"{case['name']}.graph.json",
            {"graphs": result["graphs"], "self_edges": result["self_edges"]},
        )
        if result.get("profile") is not None:
            write_json(output / f"{case['name']}.profile.json", result["profile"])
        if result["report"]:
            record["status"] = result["report"]["status"]
            record["source_count"] = len(result["report"]["sources"])
            record["views"] = {
                name: {key: value for key, value in view.items() if key != "findings"}
                | {"finding_count": len(view["findings"])}
                for name, view in result["report"]["views"].items()
            }
            record["diagnostic_codes"] = dict(
                Counter(
                    item["code"] for item in result["report"]["coverage"]["diagnostics"]
                )
            )
            record["boundary_count"] = len(result["report"]["coverage"]["boundaries"])
            old_graph_path = ARCHIVE / f"{case['original_case']}.analyzer-graph.json"
            if old_graph_path.exists():
                old = json.loads(old_graph_path.read_text())
                previous = {(item["source"], item["target"]) for item in old["edges"]}
                current = {tuple(pair) for pair in result["graphs"]["structural"]}
                record["structural_delta"] = {
                    "added": sorted(current - previous),
                    "removed": sorted(previous - current),
                }
        print(
            json.dumps(
                {
                    "case": case["name"],
                    "exit": result["exit_code"],
                    "status": record.get("status"),
                    "audit_errors": len(audit["errors"]),
                    "missing_independent": len(
                        audit.get("missing_restricted_independent_edges", [])
                    ),
                }
            ),
            flush=True,
        )
    except Exception as error:
        record = {
            "case": case,
            "exception": repr(error),
            "elapsed_seconds": time.perf_counter() - started,
        }
        print(json.dumps({"case": case["name"], "exception": repr(error)}), flush=True)
    write_json(output / f"{case['name']}.run.json", record)
    return record


def expected_deltas(output, records):
    """Check the concrete research follow-ups rather than assuming green runs."""
    checks = []
    by_name = {record["case"]["name"]: record for record in records}
    unavailable = {
        name: record.get("exception", "missing audit")
        for name, record in by_name.items()
        if "exception" in record or "audit" not in record
    }
    if not records or unavailable:
        write_json(
            output / "expected-deltas.json",
            {
                "all_passed": False,
                "checks": [
                    {
                        "name": "current_replay_reports_available",
                        "passed": False,
                        "observed": unavailable,
                    }
                ],
            },
        )
        return False

    def report(name):
        return json.loads((output / f"{name}.report.json").read_text())

    def graph(name, view="structural"):
        saved = json.loads((output / f"{name}.graph.json").read_text())
        return {tuple(pair) for pair in saved["graphs"][view]}

    def check(name, passed, observed):
        checks.append({"name": name, "passed": passed, "observed": observed})

    sympy_expected = json.loads(
        (ARCHIVE / "sympy.self-import-evidence.current.json").read_text()
    )
    self_pairs = {
        (item["module"], item["module"]) for item in sympy_expected["source_modules"]
    }
    restored = self_pairs & graph("sympy")
    saved_self = json.loads((output / "sympy.graph.json").read_text())["self_edges"]
    actual_sites = {
        (item["source"], evidence["path"], evidence["line"], evidence["source_segment"])
        for item in saved_self
        for evidence in item["evidence"]
    }
    expected_sites = {
        (item["source"], item["path"], item["line"], item["source_segment"])
        for item in sympy_expected["extracted_self_import_facts"]
    }
    check(
        "three_SymPy_self_edges_and_four_source_sites_restored",
        restored == self_pairs and expected_sites <= actual_sites,
        {
            "restored_edges": sorted(restored),
            "verified_expected_sites": len(expected_sites & actual_sites),
        },
    )
    google_before = json.loads(
        (ARCHIVE / "google-api-core-prior.current.stdout.json").read_text()
    )
    old_unresolved = [
        item
        for item in google_before["findings"]
        if item["kind"] == "unresolved_import"
    ]
    google = report("google-api-core-prior")
    new_unresolved = [
        item["finding"]
        for item in google["views"]["structural"]["findings"]
        if item["finding"]["kind"] == "unresolved_import"
    ]
    check(
        "Google_shared_namespace_false_warnings_removed",
        len(old_unresolved) == 47 and not new_unresolved,
        {"previous": len(old_unresolved), "current": len(new_unresolved)},
    )
    for name in (
        "ansible-wrong-root-diagnostic",
        "ansible-wrong-root-matched-inventory",
        "comprebuddy-standalone-root-diagnostic",
    ):
        item = report(name)
        codes = [diagnostic["code"] for diagnostic in item["coverage"]["diagnostics"]]
        check(
            name + "_rejects_wrong_root",
            item["status"] == "incomplete" and "source_root_mismatch" in codes,
            {"status": item["status"], "diagnostic_codes": codes},
        )
    multi_pairs = graph("comprebuddy-multi-root")
    expected_entrypoints = {
        (source, target)
        for source, targets in {
            "main": (
                "ingestion",
                "embeddings.vector_store",
                "agent.orchestrator",
                "feedback.loop",
            ),
            "query": ("embeddings.vector_store", "agent.orchestrator"),
            "generate_qna_bank": ("embeddings.vector_store", "agent.orchestrator"),
        }.items()
        for target in targets
    }
    multi = report("comprebuddy-multi-root")
    check(
        "CompreBuddy_multi_root_restores_eight_entrypoint_relations",
        multi["status"] == "complete" and expected_entrypoints <= multi_pairs,
        {
            "status": multi["status"],
            "restored_pairs": sorted(multi_pairs & expected_entrypoints),
        },
    )
    before_groups = components(graph("pycord-before", "module-body"))
    after_groups = components(graph("pycord-after", "module-body"))
    removed = [group for group in before_groups if group not in after_groups]
    repaired_pair = ("discord.ext.commands.help", "discord.ext.bridge")
    check(
        "Pycord_repair_visible_with_unchanged_structural_pairs",
        graph("pycord-before") == graph("pycord-after")
        and repaired_pair in graph("pycord-before", "module-body")
        and repaired_pair not in graph("pycord-after", "module-body")
        and len(removed) == 1
        and len(removed[0]) == 7,
        {
            "before_component_sizes": sorted(map(len, before_groups)),
            "after_component_sizes": sorted(map(len, after_groups)),
            "removed_components": removed,
            "repaired_pair": repaired_pair,
            "context_policy_delta": "Unlike the research heuristic, wildcard imports disable TYPE_CHECKING alias recognition conservatively; remaining components can be larger.",
        },
    )
    for name, expected_count in (
        ("ansible", 590),
        ("django", 907),
        ("sympy-initial", 880),
        ("comfyui", 708),
        ("vibeapps-wireless-networks", 3),
    ):
        item = report(name)
        check(
            name + "_matches_original_full_source_count",
            len(item["sources"]) == expected_count,
            {"source_count": len(item["sources"]), "status": item["status"]},
        )
    for name in (
        "pandas-acknowledged",
        "comfyui-supported-subset-acknowledged",
        "comfyui-full-acknowledged",
    ):
        item = report(name)
        unacknowledged = [
            target["name"]
            for target in item["coverage"]["boundaries"]
            if not target["acknowledged"]
        ]
        check(
            name + "_complete_with_explicit_boundaries",
            boundary_declarations_match(by_name[name]["case"], item),
            {
                "status": item["status"],
                "boundary_count": len(item["coverage"]["boundaries"]),
                "declared_target_count": len(by_name[name]["case"]["targets"]),
                "unacknowledged": unacknowledged,
            },
        )
    for name, view in (
        ("requests", "non-typing"),
        ("flask", "module-body"),
        ("pyspectrometer3", "module-body"),
        ("vibeapps-music", "module-body"),
        ("vibeapps-notes", "module-body"),
    ):
        item = report(name)["views"][view]
        check(
            name + "_context_sensitive_cycles_removed",
            item["cyclic_node_count"] == 0,
            {"view": view, "cyclic_node_count": item["cyclic_node_count"]},
        )
    check(
        "all_saved_restricted_exact_edges_retained",
        all(
            "audit" in record
            and not record["audit"].get("missing_restricted_independent_edges")
            for record in records
        ),
        {
            record["case"]["name"]: record.get("audit", {}).get(
                "missing_restricted_independent_edges", []
            )
            for record in records
        },
    )
    check(
        "all_reports_pass_evidence_witness_and_component_audits",
        all("audit" in record and not record["audit"]["errors"] for record in records),
        {
            record["case"]["name"]: record.get("audit", {}).get(
                "errors", [record.get("exception", "missing audit")]
            )
            for record in records
        },
    )
    hashes = {
        json.dumps(record.get("analyzer_source_sha256"), sort_keys=True)
        for record in records
    }
    check(
        "same_analyzer_sources_for_every_run",
        len(hashes) == 1
        and all(
            record.get("analyzer_source_unchanged_during_run") for record in records
        ),
        {"distinct_hash_sets": len(hashes)},
    )
    all_passed = all(item["passed"] for item in checks)
    write_json(
        output / "expected-deltas.json",
        {"all_passed": all_passed, "checks": checks},
    )
    return all_passed


def profile_comparison(output):
    baseline = json.loads((ARCHIVE / "sympy.current.profile.json").read_text())
    current = json.loads((output / "sympy.profile.json").read_text())

    def operation(functions, name, *, line=None):
        return next(
            item
            for item in functions
            if item["function"] == name and (line is None or item["line"] == line)
        )

    old_collection = operation(baseline["top_functions"], "collect")
    old_segments = operation(baseline["top_functions"], "get_source_segment")
    collection = operation(current["extraction_functions"], "collect")
    segments = operation(current["extraction_functions"], "segment")
    from pyarchgraph.adapters.extraction import _SourceText

    cache = operation(
        current["extraction_functions"],
        "__init__",
        line=_SourceText.__init__.__code__.co_firstlineno,
    )
    context = json.loads(
        (PROJECT / "benchmarks/context-optimization-2026-09-27.json").read_text()
    )
    write_json(
        output / "profile-comparison.json",
        {
            "archived_baseline": {
                "profiled_cli_seconds": baseline["elapsed_seconds_with_profiling"],
                "collection_cumulative_seconds": old_collection["cumulative_seconds"],
                "snippet_calls": old_segments["calls"],
                "snippet_cumulative_seconds": old_segments["cumulative_seconds"],
            },
            "current": {
                "profile_total_seconds": current["total_seconds"],
                "collection_cumulative_seconds": collection["cumulative_seconds"],
                "snippet_calls": segments["total_calls"],
                "snippet_cumulative_seconds": segments["cumulative_seconds"],
                "source_cache_cumulative_seconds": cache["cumulative_seconds"],
            },
            "archived_paired_context_extraction_benchmark": {
                "repeats_per_collector": context["repeats"],
                "fact_count": context["fact_count"],
                "facts_ids_contexts_diagnostics_equal": context[
                    "facts_ids_contexts_diagnostics_equal"
                ],
                "median_seconds": context["median_seconds"],
                "optimized_source_sha256": context["optimized_source_sha256"],
            },
            "interpretation": [
                "The repeated whole-source snippet splitting path has been removed; one cached extraction now serves each import statement's aliases.",
                "The archived paired context benchmark records the earlier optimization; its source hash identifies that earlier collector, not the current runtime.",
                "Archived/current profiles have different functionality and instrumentation: the current run builds three contextual views and captures supplementary graph pairs.",
                "Single profile timings are descriptive, include profiler overhead and changing machine load, and do not establish a controlled overall speedup.",
                "Cumulative function timings overlap and must not be added together.",
            ],
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--case", action="append", default=[])
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(worker(json.load(sys.stdin)), sort_keys=True))
        return 0
    before = archival_hashes()
    cases = []
    for path in sorted(ARCHIVE.glob("*.current.run.json")):
        original = json.loads(path.read_text())
        cases.append(
            {
                "name": original["case"],
                "original_case": original["case"],
                "cwd": original["cwd"],
                "roots": [original["source_root"]],
                "excludes": original["excludes"],
                "original_exit": original["exit_code"],
                "repository_commit": original["repository_commit"],
                "profile": original["case"] == "sympy",
            }
        )
    by_name = {case["name"]: case for case in cases}
    for name, source in (
        ("pandas-acknowledged", "pandas"),
        ("comfyui-supported-subset-acknowledged", "comfyui-supported-subset"),
        ("comfyui-full-acknowledged", "comfyui"),
    ):
        mapped_case = "comfyui-supported-subset" if source == "comfyui" else source
        cases.append(
            {
                **by_name[source],
                "name": name,
                "supplement": True,
                "targets": targets_for(mapped_case, acknowledged=True),
            }
        )
    cases.append(
        {
            **by_name["comprebuddy-standalone-root-diagnostic"],
            "name": "comprebuddy-multi-root",
            "roots": [".", "src"],
            "supplement": True,
        }
    )
    selected = [case for case in cases if not args.case or case["name"] in args.case]
    unknown = set(args.case) - {case["name"] for case in cases}
    if unknown:
        parser.error("unknown case: " + ", ".join(sorted(unknown)))
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "expected-deltas.json").unlink(missing_ok=True)
    write_json(args.output / "replay-cases.json", selected)
    records = [replay_case(case, args.output) for case in selected]
    deltas_passed = None
    if not args.case:
        try:
            deltas_passed = expected_deltas(args.output, records)
            if deltas_passed:
                profile_comparison(args.output)
        except Exception as error:
            deltas_passed = False
            write_json(
                args.output / "expected-deltas.json",
                {"all_passed": False, "exception": repr(error), "checks": []},
            )
    after = archival_hashes()
    all_passed = (
        bool(records)
        and all(record_passed(record) for record in records)
        and len(
            {
                json.dumps(record.get("analyzer_source_sha256"), sort_keys=True)
                for record in records
            }
        )
        == 1
        and before == after
        and deltas_passed is not False
    )
    write_json(
        args.output / "archival-integrity.json",
        {
            "unchanged": before == after,
            "before": before,
            "after": after,
            "files_checked": len(before),
            "method": "SHA-256 of all archived report/artifact bytes before and after fresh replay",
        },
    )
    write_json(
        args.output / "replay-summary.json",
        {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "original_configuration_count": sum(
                not case.get("supplement") for case in selected
            ),
            "supplementary_configuration_count": sum(
                bool(case.get("supplement")) for case in selected
            ),
            "archival_files_unchanged": before == after,
            "all_passed": all_passed,
            "expected_deltas_passed": deltas_passed,
            "records": records,
            "limitations": [
                "Target code was parsed, never executed or installed.",
                "Targets are the existing archived working directories; their recorded commit identities and source bytes were not independently reverified for this replay.",
                "Supplementary product graphs are checked with independent SCC traversal but are not independent edge oracles.",
                "Saved restricted exact-edge audits test a subset; no overall precision/recall or runtime safety claim.",
            ],
        },
    )
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
