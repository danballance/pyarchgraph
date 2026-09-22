from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from pyarchgraph import analyse
from pyarchgraph.cleanup_comparison import compare_cleanup
from pyarchgraph.rendering import render_json


def _write(root: Path, sources: dict[str, str]) -> None:
    for name, source in sources.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def _document(root: Path, *, rules: tuple[tuple[str, str], ...] = ()) -> dict:
    return json.loads(render_json(analyse(root, forbidden_dependencies=rules)))


def _atoms(records: list[dict]) -> set[tuple[str, str, str]]:
    return {(item["kind"], item["source"], item["target"]) for item in records}


@pytest.fixture
def cycle(tmp_path: Path) -> dict:
    _write(tmp_path, {"a.py": "import b\n", "b.py": "import a\n"})
    return _document(tmp_path)


def test_new_violation_is_visible_when_net_count_improves(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "a.py": "import b\n",
            "b.py": "import c\n",
            "c.py": "import a\n",
            "d.py": "",
        },
    )
    baseline = _document(tmp_path)
    _write(tmp_path, {"b.py": "", "d.py": "import d\n"})

    comparison = compare_cleanup(_document(tmp_path), baseline)

    assert comparison["before_violation_count"] == 3
    assert comparison["current_violation_count"] == 1
    assert comparison["count_delta"] == -2
    assert comparison["has_new_violations"]
    assert _atoms(comparison["newly_observed"]) == {("cyclic_dependency", "d", "d")}
    assert len(comparison["verified_resolved"]) == 3
    assert not comparison["needs_review"]


def test_new_cycle_marks_existing_edges_as_new_violations(tmp_path: Path) -> None:
    _write(tmp_path, {"a.py": "import b\n", "b.py": ""})
    baseline = _document(tmp_path)
    (tmp_path / "b.py").write_text("import a\n", encoding="utf-8")

    comparison = compare_cleanup(_document(tmp_path), baseline)

    assert comparison["count_delta"] == 2
    assert _atoms(comparison["newly_observed"]) == {
        ("cyclic_dependency", "a", "b"),
        ("cyclic_dependency", "b", "a"),
    }


def test_deleted_intermediate_module_does_not_verify_surviving_cycle_edge(
    tmp_path: Path,
) -> None:
    _write(tmp_path, {"a.py": "import b\n", "b.py": "import c\n", "c.py": "import a\n"})
    baseline = _document(tmp_path)
    (tmp_path / "c.py").unlink()
    current = _document(tmp_path)
    # The current resolver cannot distinguish this missing flat module from an
    # external dependency. Inventory history must therefore prevent false credit.
    assert current["analysis"]["dependency_resolution_complete"]
    comparison = compare_cleanup(current, baseline, allow_inventory_change=True)
    assert comparison["verified_resolved"] == []
    assert _atoms(comparison["disappeared_unverified"]) == {
        ("cyclic_dependency", "a", "b")
    }
    assert _atoms(comparison["removed_with_module"]) == {
        ("cyclic_dependency", "b", "c"),
        ("cyclic_dependency", "c", "a"),
    }
    assert comparison["needs_review"]


def test_resolution_credit_is_conservative_even_for_unrelated_module_deletion(
    tmp_path: Path,
) -> None:
    _write(tmp_path, {"a.py": "import b\n", "b.py": "import a\n", "c.py": ""})
    baseline = _document(tmp_path)
    (tmp_path / "b.py").write_text("", encoding="utf-8")
    (tmp_path / "c.py").unlink()
    comparison = compare_cleanup(
        _document(tmp_path), baseline, allow_inventory_change=True
    )
    assert comparison["verified_resolved"] == []
    assert len(comparison["disappeared_unverified"]) == 2
    assert comparison["removed_with_module"] == []
    assert comparison["needs_review"]


def test_forbidden_and_cyclic_atoms_on_same_pair_remain_distinct(
    tmp_path: Path,
) -> None:
    rules = (("a", "b"), ("a", "*"))
    _write(tmp_path, {"a.py": "import b\n", "b.py": "import a\n"})
    baseline = _document(tmp_path, rules=rules)
    (tmp_path / "b.py").write_text("", encoding="utf-8")

    comparison = compare_cleanup(_document(tmp_path, rules=rules), baseline)

    assert comparison["before_violation_count"] == 3
    assert comparison["current_violation_count"] == 1
    assert len(comparison["verified_resolved"]) == 2
    assert _atoms(comparison["persistent"]) == {("forbidden_dependency", "a", "b")}


def test_forbidden_atom_is_new_even_when_another_is_resolved(tmp_path: Path) -> None:
    rules = (("*", "c"),)
    _write(tmp_path, {"a.py": "import c\n", "b.py": "", "c.py": ""})
    baseline = _document(tmp_path, rules=rules)
    _write(tmp_path, {"a.py": "", "b.py": "import c\n"})

    comparison = compare_cleanup(_document(tmp_path, rules=rules), baseline)

    assert comparison["count_delta"] == 0
    assert comparison["has_new_violations"]
    assert _atoms(comparison["newly_observed"]) == {("forbidden_dependency", "b", "c")}
    assert _atoms(comparison["verified_resolved"]) == {
        ("forbidden_dependency", "a", "c")
    }


@pytest.mark.parametrize("promote", [False, True])
def test_certainty_changes_do_not_create_or_resolve_atoms(
    tmp_path: Path, promote: bool
) -> None:
    definite = "import b\n"
    possible = 'from importlib import import_module\nimport_module("b")\n'
    rules = (("a", "b"),)
    _write(tmp_path, {"a.py": possible if promote else definite, "b.py": "import a\n"})
    baseline = _document(tmp_path, rules=rules)
    (tmp_path / "a.py").write_text(definite if promote else possible, encoding="utf-8")

    comparison = compare_cleanup(_document(tmp_path, rules=rules), baseline)

    key = "newly_confirmed" if promote else "lost_certainty"
    assert len(comparison[key]) == 3
    assert comparison["count_delta"] == (3 if promote else -3)
    assert comparison["newly_observed"] == []
    assert comparison["verified_resolved"] == []
    assert comparison["disappeared_unverified"] == []
    assert comparison["persistent"] == []
    assert not comparison["has_new_violations"]
    assert comparison["needs_review"]
    assert {record["certainty"] for record in comparison[key]} == {
        "definite" if promote else "possible"
    }


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 syntax")
def test_type_alias_dynamic_import_is_lost_certainty_not_repair(
    tmp_path: Path, cycle: dict
) -> None:
    (tmp_path / "a.py").write_text(
        'from importlib import import_module\ntype Alias = import_module("b")\n',
        encoding="utf-8",
    )
    current = _document(tmp_path)

    comparison = compare_cleanup(current, cycle)

    assert current["analysis"]["complete"]
    assert comparison["current_violation_count"] == 0
    assert len(comparison["lost_certainty"]) == 2
    assert comparison["verified_resolved"] == []
    assert comparison["needs_review"]


def test_new_possible_atom_is_flagged_despite_zero_known_debt(tmp_path: Path) -> None:
    _write(tmp_path, {"a.py": "", "b.py": "import a\n"})
    baseline = _document(tmp_path)
    (tmp_path / "a.py").write_text('__import__("b")\n', encoding="utf-8")

    comparison = compare_cleanup(_document(tmp_path), baseline)

    assert comparison["count_delta"] == 0
    assert comparison["has_new_violations"]
    assert len(comparison["newly_observed"]) == 2
    assert {item["certainty"] for item in comparison["newly_observed"]} == {"possible"}
    assert comparison["needs_review"]


def test_clean_unrelated_growth_cannot_dilute_existing_debt(
    tmp_path: Path, cycle: dict
) -> None:
    _write(tmp_path, {"c.py": "import d\n", "d.py": ""})

    comparison = compare_cleanup(
        _document(tmp_path), cycle, allow_inventory_change=True
    )

    assert comparison["count_delta"] == 0
    assert len(comparison["persistent"]) == 2
    assert comparison["newly_observed"] == []
    assert comparison["needs_review"]


def test_splitting_component_does_not_invent_violations(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "a.py": "import b\n",
            "b.py": "import a\nimport c\n",
            "c.py": "import d\n",
            "d.py": "import c\nimport a\n",
        },
    )
    baseline = _document(tmp_path)
    (tmp_path / "d.py").write_text("import c\n", encoding="utf-8")

    comparison = compare_cleanup(_document(tmp_path), baseline)

    assert comparison["count_delta"] == -2
    assert len(comparison["persistent"]) == 4
    assert len(comparison["verified_resolved"]) == 2
    assert not comparison["has_new_violations"]


def test_partial_parse_cannot_verify_disappearing_cycle(
    tmp_path: Path, cycle: dict
) -> None:
    (tmp_path / "a.py").write_text("def broken(\n", encoding="utf-8")

    comparison = compare_cleanup(_document(tmp_path), cycle)

    assert len(comparison["disappeared_unverified"]) == 2
    assert comparison["verified_resolved"] == []
    assert comparison["needs_review"]


@pytest.mark.parametrize("side", ["current", "baseline"])
@pytest.mark.parametrize(
    "field", ["complete", "scope_valid", "dependency_resolution_complete"]
)
def test_partial_coverage_is_comparable_but_requires_review(
    cycle: dict, side: str, field: str
) -> None:
    baseline, current = deepcopy(cycle), deepcopy(cycle)
    analysis = (current if side == "current" else baseline)["analysis"]
    analysis[field] = False
    analysis["dependency_resolution_complete"] = False

    comparison = compare_cleanup(current, baseline)

    assert comparison["compatible"]
    assert comparison["needs_review"]
    assert len(comparison["persistent"]) == 2


def test_empty_inventories_can_be_compared_without_claiming_completion(
    tmp_path: Path,
) -> None:
    empty = _document(tmp_path)

    comparison = compare_cleanup(empty, empty)

    assert comparison["compatible"]
    assert comparison["current_violation_count"] == 0
    assert comparison["needs_review"]


def test_removed_endpoints_take_precedence_over_incomplete_coverage(
    tmp_path: Path, cycle: dict
) -> None:
    (tmp_path / "b.py").unlink()
    (tmp_path / "a.py").write_text("def broken(\n", encoding="utf-8")
    current = _document(tmp_path)
    assert not current["analysis"]["dependency_resolution_complete"]

    with pytest.raises(ValueError, match="module inventory changed"):
        compare_cleanup(current, cycle)
    comparison = compare_cleanup(current, cycle, allow_inventory_change=True)

    assert comparison["removed_modules"] == [["b", "b.py"]]
    assert len(comparison["removed_with_module"]) == 2
    assert comparison["verified_resolved"] == []
    assert comparison["disappeared_unverified"] == []
    assert comparison["needs_review"]


def test_rename_is_reported_as_removed_and_new_without_guessing_identity(
    tmp_path: Path, cycle: dict
) -> None:
    (tmp_path / "b.py").rename(tmp_path / "c.py")
    (tmp_path / "a.py").write_text("import c\n", encoding="utf-8")

    comparison = compare_cleanup(
        _document(tmp_path), cycle, allow_inventory_change=True
    )

    assert comparison["count_delta"] == 0
    assert len(comparison["removed_with_module"]) == 2
    assert len(comparison["newly_observed"]) == 2
    assert comparison["has_new_violations"]
    assert comparison["needs_review"]


def test_saved_cleanup_counts_and_lists_cannot_erase_graph_debt(cycle: dict) -> None:
    baseline, current = deepcopy(cycle), deepcopy(cycle)
    for document in (baseline, current):
        document["cleanup"].update(
            violation_count=-100, violations=[], cleanup_complete=True
        )

    comparison = compare_cleanup(current, baseline)

    assert (
        comparison["before_violation_count"]
        == comparison["current_violation_count"]
        == 2
    )
    assert len(comparison["persistent"]) == 2


def test_comparison_does_not_mutate_or_alias_input_documents(
    tmp_path: Path, cycle: dict
) -> None:
    (tmp_path / "a.py").write_text("\n\nimport b\n", encoding="utf-8")
    current = _document(tmp_path)
    saved_current, saved_baseline = deepcopy(current), deepcopy(cycle)

    comparison = compare_cleanup(current, cycle)

    assert comparison == compare_cleanup(current, cycle)
    assert current == saved_current
    assert cycle == saved_baseline
    comparison["persistent"][0]["certainty"] = "changed by caller"
    assert current == saved_current
    assert cycle == saved_baseline


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_root", "elsewhere"),
        ("python_version", "0.0.0"),
        ("excludes", ["other"]),
        ("graph_policy_version", "future"),
        ("formula_version", "future"),
        ("analyser", {"version": "other", "commit": None, "source_digest": "other"}),
    ],
)
def test_provenance_differences_remain_incompatible(
    cycle: dict, field: str, value
) -> None:
    current = deepcopy(cycle)
    current["analysis"]["provenance"][field] = value
    with pytest.raises(ValueError, match=field):
        compare_cleanup(current, cycle, allow_inventory_change=True)


@pytest.mark.parametrize("side", ["current", "baseline"])
@pytest.mark.parametrize("schema", ["0.3", "0.5"])
def test_only_cleanup_schema_is_accepted(cycle: dict, side: str, schema: str) -> None:
    baseline, current = deepcopy(cycle), deepcopy(cycle)
    (current if side == "current" else baseline)["schema_version"] = schema
    with pytest.raises(ValueError, match="schema_version"):
        compare_cleanup(current, baseline)


@pytest.mark.parametrize("field", ["cleanup", "provenance"])
def test_cleanup_model_version_is_required(cycle: dict, field: str) -> None:
    baseline = deepcopy(cycle)
    if field == "cleanup":
        baseline["cleanup"]["model_version"] = "future"
    else:
        del baseline["analysis"]["provenance"]["cleanup_model_version"]
    with pytest.raises(ValueError, match="model_version"):
        compare_cleanup(cycle, baseline)


@pytest.mark.parametrize("invalid", [None, [], "baseline", 1])
def test_malformed_baseline_is_rejected(cycle: dict, invalid) -> None:
    with pytest.raises(ValueError, match="incompatible baseline"):
        compare_cleanup(cycle, invalid)


def test_missing_findings_cannot_hide_recorded_cycle(cycle: dict) -> None:
    baseline = deepcopy(cycle)
    baseline["findings"] = []
    with pytest.raises(ValueError, match="cycle components do not match graph"):
        compare_cleanup(cycle, baseline)


@pytest.mark.parametrize(
    ("severity", "code", "field"),
    [
        ("error", "source_read_failed", "complete"),
        ("warning", "expected_package_missing", "scope_valid"),
        ("warning", "source_root_mismatch", "scope_valid"),
        ("warning", "dynamic_import_ignored", "dependency_resolution_complete"),
    ],
)
def test_diagnostic_coverage_cannot_be_overridden_by_saved_flags(
    cycle: dict, severity: str, code: str, field: str
) -> None:
    baseline = deepcopy(cycle)
    baseline["diagnostics"].append(
        {"severity": severity, "code": code, "message": code}
    )
    with pytest.raises(ValueError, match=rf"analysis\.{field}"):
        compare_cleanup(cycle, baseline)


@pytest.mark.parametrize("field", ["complete", "scope_valid"])
def test_resolution_cannot_be_complete_when_analysis_is_not(
    cycle: dict, field: str
) -> None:
    baseline = deepcopy(cycle)
    baseline["analysis"][field] = False
    with pytest.raises(ValueError, match=r"analysis\.dependency_resolution_complete"):
        compare_cleanup(cycle, baseline)


@pytest.mark.parametrize("reason", ["missing_internal_target", "relative_escape"])
def test_unresolved_coverage_cannot_be_overridden_by_saved_flags(
    cycle: dict, reason: str
) -> None:
    baseline = deepcopy(cycle)
    fact = next(fact for fact in baseline["import_facts"] if fact["source"] == "a")
    baseline["unresolved_imports"].append(
        {
            "source": "a",
            "requested": "a.missing",
            "reason": reason,
            "fact_ids": [fact["id"]],
        }
    )
    with pytest.raises(ValueError, match=r"analysis\.dependency_resolution_complete"):
        compare_cleanup(cycle, baseline)


def test_namespace_base_alone_does_not_make_comparison_uncertain(
    tmp_path: Path,
) -> None:
    _write(tmp_path, {"pkg/a.py": "", "consumer.py": "import pkg\n"})
    document = _document(tmp_path)
    assert document["unresolved_imports"][0]["reason"] == "namespace_base_unmodelled"
    assert document["analysis"]["dependency_resolution_complete"]

    comparison = compare_cleanup(document, document)

    assert not comparison["needs_review"]


def test_probable_edges_cannot_claim_complete_resolution(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {"pkg/__init__.py": "", "pkg/a.py": "from . import b\n", "pkg/b.py": ""},
    )
    document = _document(tmp_path)
    assert not document["analysis"]["dependency_resolution_complete"]
    assert document["diagnostics"] == []
    forged = deepcopy(document)
    forged["analysis"]["dependency_resolution_complete"] = True
    with pytest.raises(ValueError, match=r"analysis\.dependency_resolution_complete"):
        compare_cleanup(forged, document)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("diagnostics", None),
        ("diagnostics", [None]),
        ("diagnostics", [{"severity": "info", "code": "x", "message": "x"}]),
        (
            "diagnostics",
            [{"severity": "warning", "code": "x", "message": "x", "line": True}],
        ),
        ("unresolved_imports", None),
        ("unresolved_imports", [None]),
        (
            "unresolved_imports",
            [{"source": "a", "requested": "b", "reason": "unknown", "fact_ids": []}],
        ),
        (
            "unresolved_imports",
            [
                {
                    "source": "a",
                    "requested": "b",
                    "reason": "missing_internal_target",
                    "fact_ids": ["missing"],
                }
            ],
        ),
        ("limitations", {}),
        ("check", None),
        ("check", {}),
        ("check", {"status": "pass"}),
    ],
)
def test_malformed_or_inconsistent_coverage_is_rejected(
    cycle: dict, field: str, value
) -> None:
    baseline = deepcopy(cycle)
    baseline[field] = value
    with pytest.raises(ValueError, match=field):
        compare_cleanup(cycle, baseline)
