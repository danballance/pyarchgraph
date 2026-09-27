"""Check every committed dependency scenario through the real CLI.

Run from the repository root: python -m examples.evaluate [--json].
Fixture applications are read as source and are never imported or executed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

EXAMPLES = Path(__file__).resolve().parent
REPOSITORY = EXAMPLES.parent


def load_manifest() -> dict[str, Any]:
    return json.loads((EXAMPLES / "manifest.json").read_text(encoding="utf-8"))


def iter_runs(project_ids: list[str] | None = None):
    """Yield each project and its complete default/variant configuration."""
    for project in load_manifest()["projects"]:
        if project_ids and project["id"] not in project_ids:
            continue
        yield project, None, project
        for variant in project.get("variants", []):
            yield project, variant["id"], variant


def run_cli(
    project_id: str, variant_id: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Analyze one configured run with the current Python interpreter."""
    _, _, config = next(
        item for item in iter_runs([project_id]) if item[1] == variant_id
    )
    project_dir = EXAMPLES / "projects" / project_id
    command = [
        sys.executable,
        "-m",
        "pyarchgraph",
        *(str(project_dir / root) for root in config["source_roots"]),
        "--gate",
        config["gate"],
        "--details",
        config["details"],
    ]
    if config["config"] is not None:
        command.extend(["--config", str(project_dir / config["config"])])
    for pattern in config["exclusions"]:
        command.extend(["--exclude", pattern])
    return subprocess.run(
        command,
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and _matches(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(_matches(left, right) for left, right in zip(actual, expected, strict=True))
        )
    return actual == expected


def semantic_view(report: dict[str, Any], view: str | None = None) -> dict[str, Any]:
    """Translate opaque source IDs for readable, independent corpus expectations."""
    labels = {
        source["id"]: source["import_name"] or source["path"]
        for source in report["sources"]
    }

    def labelled(value):
        if isinstance(value, list):
            return [labelled(item) for item in value]
        if isinstance(value, dict):
            return {
                key: sorted(labelled(item))
                if key in ("members", "definite_members")
                else labelled(item)
                for key, item in value.items()
            }
        if isinstance(value, str):
            return labels.get(value, value)
        return value

    selected = report["views"][view or report["gate"].replace("-", "_")]
    return {"module_count": len(report["sources"]), **labelled(selected)}


def check_result(
    completed: subprocess.CompletedProcess[str], expected: dict[str, Any]
) -> list[str]:
    """Compare the real report with independently specified expectations."""
    errors = []
    if completed.returncode != expected["exit_code"]:
        errors.append(f"exit {completed.returncode}, expected {expected['exit_code']}")
    if completed.stderr:
        errors.append(f"unexpected stderr: {completed.stderr.strip()}")
    try:
        report = json.loads(completed.stdout)
    except (ValueError, TypeError):
        return [*errors, "stdout is not a JSON report"]
    if not isinstance(report, dict) or set(report) != {
        "schema_version",
        "status",
        "gate",
        "sources",
        "coverage",
        "views",
    }:
        return [*errors, "report has an invalid top-level contract"]
    if report["schema_version"] != "0.6":
        errors.append("schema_version must be '0.6'")
    if report["status"] != expected["status"]:
        errors.append(
            f"status is {report['status']!r}, expected {expected['status']!r}"
        )
    if set(report["views"]) != {"structural", "non_typing", "module_body"}:
        return [*errors, "report must contain exactly the three graph views"]
    actual_codes = {item["code"] for item in report["coverage"]["diagnostics"]}
    for code in expected.get("diagnostics", []):
        if code not in actual_codes:
            errors.append(f"missing coverage diagnostic {code}")
    semantic = semantic_view(report)
    for field in ("module_count", "dependency_count"):
        if type(semantic[field]) is not int or semantic[field] != expected[field]:
            errors.append(f"{field} is {semantic[field]!r}, expected {expected[field]}")
    remaining = list(semantic["findings"])
    for finding in expected["findings"]:
        for index, actual in enumerate(remaining):
            if _matches(actual, finding):
                remaining.pop(index)
                break
        else:
            errors.append(
                f"missing expected finding: {json.dumps(finding, sort_keys=True)}"
            )
    if remaining:
        errors.append(f"unexpected findings: {json.dumps(remaining, sort_keys=True)}")
    return errors


def evaluate(project_ids: list[str] | None = None) -> dict[str, Any]:
    rows = []
    for project, variant_id, config in iter_runs(project_ids):
        expected = config["expected"]
        completed = run_cli(project["id"], variant_id)
        errors = check_result(completed, expected)
        rows.append(
            {
                "project": project["id"],
                "variant": variant_id,
                "expected": expected["outcome"],
                "actual": {0: "pass", 1: "fail", 2: "error"}.get(
                    completed.returncode, f"exit {completed.returncode}"
                ),
                "matched": not errors,
                "errors": errors,
            }
        )
    return {"matched": all(row["matched"] for row in rows), "results": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", action="store_true", help="print acceptance results as JSON"
    )
    parser.add_argument(
        "--project",
        action="append",
        choices=[item["id"] for item in load_manifest()["projects"]],
        help="check this project and its variants; repeatable",
    )
    args = parser.parse_args()
    report = evaluate(args.project)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    else:
        table = [["Project / variant", "Expected", "Actual", "Acceptance"]]
        for row in report["results"]:
            label = row["project"] + (f"/{row['variant']}" if row["variant"] else "")
            table.append(
                [
                    label,
                    row["expected"],
                    row["actual"],
                    "OK" if row["matched"] else "MISMATCH",
                ]
            )
        widths = [max(len(row[column]) for row in table) for column in range(4)]
        print(f"Architecture corpus: {len(report['results'])} runs")
        for row in table:
            print("  ".join(value.ljust(width) for value, width in zip(row, widths, strict=True)))
        for row in report["results"]:
            for error in row["errors"]:
                print(f"{row['project']}/{row['variant'] or 'default'}: {error}")
        print(
            "All expectations matched."
            if report["matched"]
            else "Acceptance expectations did not match."
        )
    return 0 if report["matched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
