"""Print current corpus results beside the immutable reviewed measurements.

Run from the repository root: python -m examples.evaluate [--json].
All project code is read as source; no fixture application is imported.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

from pyarchgraph import analyse, render_json
from pyarchgraph.findings import check_status
from pyarchgraph.policy import GraphPolicy


EXAMPLES = Path(__file__).resolve().parent


def evaluate(project_ids: list[str] | None = None) -> dict:
    """Use every root, exclusion and graph policy declared in the manifest."""
    manifest = json.loads((EXAMPLES / "manifest.json").read_text(encoding="utf-8"))
    baseline = json.loads(
        (EXAMPLES / manifest["review_baseline"]).read_text(encoding="utf-8")
    )
    previous = {
        (row["project"], row["variant"]): row for row in baseline["observations"]
    }
    rows = []
    for project in manifest["projects"]:
        if project_ids and project["id"] not in project_ids:
            continue
        project_dir = EXAMPLES / "projects" / project["id"]
        for variant in [None, *project.get("variants", [])]:
            config = project if variant is None else variant
            variant_id = None if variant is None else variant["id"]
            desired = config["desired"]
            rules = desired.get("forbidden_dependencies", []) + desired.get(
                "directional_violations", []
            )
            result = analyse(
                project_dir / config["source_root"],
                project_root=project_dir,
                excludes=tuple(config["exclusions"]),
                expected_packages=tuple(config["expected_packages"]),
                policy=GraphPolicy(**config["graph_policy"]),
                forbidden_dependencies=tuple(tuple(rule) for rule in rules),
            )
            cleanup = json.loads(render_json(result))["cleanup"]
            old = previous[(project["id"], variant_id)]
            rows.append(
                {
                    "project": project["id"],
                    "variant": variant_id,
                    "category": project["category"],
                    "baseline_score": old["score"],
                    "baseline_cyclic_module_count": old["metrics"][
                        "cyclic_module_count"
                    ],
                    "current_score": result.quality.score,
                    "current_metrics": asdict(result.quality.metrics),
                    "cleanup_violation_count": cleanup["violation_count"],
                    "cleanup_possible_violation_count": cleanup[
                        "possible_violation_count"
                    ],
                    "cleanup_counts": cleanup["counts"],
                    "cleanup_complete": cleanup["cleanup_complete"],
                    "definite_findings": sum(
                        item["certainty"] == "definite" for item in result.findings
                    ),
                    "possible_findings": sum(
                        item["certainty"] == "possible" for item in result.findings
                    ),
                    "check_status": check_status(result),
                    "scope_valid": result.scope_valid,
                    "dependency_resolution_complete": result.dependency_resolution_complete,
                    "dynamic_import_warning_count": result.quality.dynamic_import_warning_count,
                    "current_provenance": result.provenance,
                }
            )
    return {
        "reviewed_analyser": {
            "version": baseline["analyser_version"],
            "commit": baseline["analyser_commit"],
            "formula_version": baseline["formula_version"],
            "python_version": baseline["python_version"],
        },
        "results": rows,
    }


def main() -> None:
    manifest = json.loads((EXAMPLES / "manifest.json").read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        action="store_true",
        help="print results and current provenance as JSON",
    )
    parser.add_argument(
        "--project",
        action="append",
        choices=[item["id"] for item in manifest["projects"]],
        help="show this project and its variants; repeatable",
    )
    args = parser.parse_args()
    report = evaluate(args.project)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
        return

    def score(value):
        return "unavailable" if value is None else f"{value:.4f}"

    table = [
        [
            "Project / variant", "Reviewed", "Current", "Debt", "Possible debt",
            "Definite findings", "Possible findings", "Check",
        ]
    ]
    for row in report["results"]:
        label = row["project"] + (f"/{row['variant']}" if row["variant"] else "")
        table.append(
            [
                label,
                score(row["baseline_score"]),
                score(row["current_score"]),
                str(row["cleanup_violation_count"]),
                str(row["cleanup_possible_violation_count"]),
                str(row["definite_findings"]),
                str(row["possible_findings"]),
                row["check_status"],
            ]
        )
    widths = [max(len(row[column]) for row in table) for column in range(len(table[0]))]
    print(f"Architecture corpus: {len(report['results'])} runs")
    print(f"Reviewed implementation: {report['reviewed_analyser']['commit']}")
    print()
    for index, row in enumerate(table):
        print("  ".join(value.ljust(width) for value, width in zip(row, widths)))
        if index == 0:
            print("  ".join("-" * width for width in widths))
    print()
    print(
        "Debt counts definite policy violations; possible debt is separate. A lower total alone does not prove a safe edit."
    )
    print(
        "Debt counts cyclic and forbidden dependencies; cycle findings group components. Zero debt is complete only when the check passes."
    )
    print(
        "Reviewed/current scores remain advisory: padding can raise a score without resolving the original debt."
    )
    print(
        "Manifest graph settings are explicit; tests are excluded unless the run deliberately includes them."
    )


if __name__ == "__main__":
    main()
