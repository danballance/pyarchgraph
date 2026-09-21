from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from pyarchgraph.analysis import analyse
from pyarchgraph.findings import build_findings
from pyarchgraph import provenance
from pyarchgraph.provenance import compare_baseline
from pyarchgraph.rendering import render_json


def _write(root: Path, files: dict[str, str]) -> None:
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def _document(root: Path) -> dict:
    return json.loads(render_json(analyse(root)))


@pytest.fixture
def baseline(tmp_path: Path) -> dict:
    _write(tmp_path, {"a.py": "import b\n", "b.py": "", "c.py": ""})
    return _document(tmp_path)


def test_baseline_compares_semantic_dependencies_and_marks_new_witness_edges(
    tmp_path: Path,
    baseline: dict,
) -> None:
    (tmp_path / "b.py").write_text("import a\n", encoding="utf-8")
    current = _document(tmp_path)

    comparison = compare_baseline(current, baseline)

    assert comparison["compatible"]
    assert comparison["added_dependencies"] == [["b", "a"]]
    assert comparison["removed_dependencies"] == []
    assert comparison["new_definite_cyclic_dependencies"] == [["a", "b"], ["b", "a"]]
    assert {
        (edge["source"], edge["target"]): edge["new_dependency"]
        for finding in current["findings"]
        for edge in finding["witness"]
    } == {("a", "b"): False, ("b", "a"): True}
    assert baseline["findings"] == []


def test_blank_lines_do_not_create_baseline_dependency_changes(tmp_path: Path) -> None:
    _write(tmp_path, {"a.py": "import b\n", "b.py": "import a\n"})
    baseline = _document(tmp_path)
    _write(tmp_path, {"a.py": "\n\nimport b\n", "b.py": "\nimport a\n"})
    current = _document(tmp_path)

    comparison = compare_baseline(current, baseline)

    for key in (
        "added_dependencies",
        "removed_dependencies",
        "new_definite_cyclic_dependencies",
        "resolved_definite_cyclic_dependencies",
        "added_modules",
        "removed_modules",
    ):
        assert comparison[key] == []
    assert current["findings"][0]["id"] == baseline["findings"][0]["id"]


def test_probable_to_definite_cycle_promotion_is_detected_without_new_edges(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/a.py": "from . import b\n",
            "pkg/b.py": "import pkg.a\n",
        },
    )
    baseline = _document(tmp_path)
    (tmp_path / "pkg/a.py").write_text("import pkg.b\n", encoding="utf-8")
    current = _document(tmp_path)

    comparison = compare_baseline(current, baseline)

    assert baseline["findings"][0]["certainty"] == "possible"
    assert current["findings"][0]["certainty"] == "definite"
    assert comparison["added_dependencies"] == []
    assert comparison["removed_dependencies"] == []
    assert comparison["new_definite_cyclic_dependencies"] == [
        ["pkg.a", "pkg.b"],
        ["pkg.b", "pkg.a"],
    ]


def test_removing_cycle_edge_reports_resolved_definite_cyclic_relationships(
    tmp_path: Path,
) -> None:
    _write(tmp_path, {"a.py": "import b\n", "b.py": "import a\n"})
    baseline = _document(tmp_path)
    (tmp_path / "b.py").write_text("", encoding="utf-8")

    comparison = compare_baseline(_document(tmp_path), baseline)

    assert comparison["removed_dependencies"] == [["b", "a"]]
    assert comparison["resolved_definite_cyclic_dependencies"] == [
        ["a", "b"],
        ["b", "a"],
    ]


@pytest.mark.parametrize(
    ("path", "changed_value"),
    [
        (("source_root",), "different/src"),
        (("excludes",), ["another_package"]),
        (("default_excluded_directories",), ["new_default"]),
        (("graph_policy", "include_type_only"), False),
        (("graph_policy", "include_local"), False),
        (("graph_policy", "include_tests"), True),
        (("expected_packages",), ["different_package"]),
        (("python_version",), "0.0.0"),
        (("analyser", "version"), "different_version"),
        (("analyser", "commit"), "different_commit"),
        (("analyser", "source_digest"), "different_source_digest"),
        (("formula_version",), "future_formula"),
        (("graph_policy_version",), "future_policy"),
        (("forbidden_dependencies",), [["a", "b"]]),
        (("fact_source",), "another_fact_source"),
    ],
)
def test_baseline_rejects_changed_analysis_provenance(
    baseline: dict,
    path: tuple[str, ...],
    changed_value,
) -> None:
    current = deepcopy(baseline)
    parent = current["analysis"]["provenance"]
    for part in path[:-1]:
        parent = parent[part]
    parent[path[-1]] = changed_value

    with pytest.raises(ValueError, match=path[0]):
        compare_baseline(current, baseline, allow_inventory_change=True)


def test_inventory_additions_and_removals_require_explicit_opt_in(
    tmp_path: Path,
    baseline: dict,
) -> None:
    (tmp_path / "c.py").unlink()
    (tmp_path / "d.py").write_text("", encoding="utf-8")
    current = _document(tmp_path)

    with pytest.raises(ValueError, match="module inventory changed"):
        compare_baseline(current, baseline)
    comparison = compare_baseline(current, baseline, allow_inventory_change=True)

    assert comparison["added_modules"] == [["d", "d.py"]]
    assert comparison["removed_modules"] == [["c", "c.py"]]


@pytest.mark.parametrize("side", ["current", "baseline"])
@pytest.mark.parametrize("field", ["complete", "scope_valid"])
def test_incomplete_or_invalid_scope_cannot_be_compared(
    baseline: dict,
    side: str,
    field: str,
) -> None:
    current = deepcopy(baseline)
    document = current if side == "current" else baseline
    document["analysis"][field] = False

    with pytest.raises(ValueError, match="incomplete or has invalid scope"):
        compare_baseline(current, baseline, allow_inventory_change=True)


@pytest.mark.parametrize("side", ["current", "baseline"])
def test_empty_inventory_cannot_be_accepted_by_inventory_opt_in(
    baseline: dict, side: str
) -> None:
    current = deepcopy(baseline)
    document = current if side == "current" else baseline
    document["modules"] = []

    with pytest.raises(ValueError, match="empty inventory"):
        compare_baseline(current, baseline, allow_inventory_change=True)


def test_baseline_rejects_schema_mismatch(baseline: dict) -> None:
    current = deepcopy(baseline)
    current["schema_version"] = "different"

    with pytest.raises(ValueError, match="schema_version"):
        compare_baseline(current, baseline)


def test_baseline_requires_provenance(baseline: dict) -> None:
    current = deepcopy(baseline)
    del baseline["analysis"]["provenance"]

    with pytest.raises(ValueError, match="provenance is missing"):
        compare_baseline(current, baseline)


@pytest.mark.parametrize(
    ("path", "invalid_value"),
    [
        (("schema_version",), None),
        (("analysis",), None),
        (("analysis",), []),
        (("analysis", "complete"), "true"),
        (("analysis", "scope_valid"), 1),
        (("analysis", "dependency_resolution_complete"), {}),
        (("analysis", "provenance"), []),
        (("analysis", "provenance"), {}),
        (("analysis", "provenance", "analyser"), None),
        (("analysis", "provenance", "analyser", "source_digest"), 123),
        (("analysis", "provenance", "graph_policy"), []),
        (("analysis", "provenance", "graph_policy", "include_local"), 1),
        (("analysis", "provenance", "forbidden_dependencies"), ["a"]),
        (("modules",), [42]),
        (("modules", 0, "id"), []),
        (("modules", 0, "path"), None),
        (("modules", 0, "is_package"), "false"),
        (("import_facts",), None),
        (("import_facts", 0, "source"), {}),
        (("import_facts", 0, "line"), True),
        (("architecture_dependencies",), {}),
        (("architecture_dependencies", 0, "source"), []),
        (("architecture_dependencies", 0, "target"), "not_in_inventory"),
        (("architecture_dependencies", 0, "evidence"), []),
        (("architecture_dependencies", 0, "evidence", 0, "fact_id"), "missing_fact"),
        (("architecture_dependencies", 0, "evidence", 0, "resolution_kind"), {}),
        (("findings",), None),
        (("findings",), []),
        (("findings",), {}),
        (("findings",), [{"kind": "cycle"}]),
        (("findings", 0, "kind"), "unrecognized_finding"),
        (("findings", 0, "members"), ["not_in_inventory"]),
        (("findings", 0, "certainty"), "maybe"),
        (("findings", 0, "cyclic_dependencies"), [["a"]]),
        (("findings", 0, "definite_cyclic_dependencies"), []),
        (("findings", 0, "definite_members"), []),
        (("findings", 0, "witness"), []),
        (("findings", 0, "witness", 0, "evidence", 0, "line"), 100),
    ],
)
def test_malformed_baseline_reports_incompatibility_instead_of_crashing_or_clearing_cycles(
    tmp_path: Path,
    path: tuple,
    invalid_value,
) -> None:
    _write(tmp_path, {"a.py": "import b\n", "b.py": "import a\n"})
    current = _document(tmp_path)
    baseline = deepcopy(current)
    parent = baseline
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = invalid_value

    with pytest.raises(ValueError, match="incompatible baseline"):
        compare_baseline(current, baseline)


@pytest.mark.parametrize("invalid_document", [None, [], "baseline", 1])
def test_baseline_document_must_be_an_object(baseline: dict, invalid_document) -> None:
    with pytest.raises(ValueError, match="incompatible baseline"):
        compare_baseline(baseline, invalid_document)


@pytest.mark.parametrize(
    "field", ["findings", "architecture_dependencies", "import_facts"]
)
def test_baseline_cannot_omit_required_graph_data(baseline: dict, field: str) -> None:
    current = deepcopy(baseline)
    del baseline[field]

    with pytest.raises(ValueError, match="incompatible baseline"):
        compare_baseline(current, baseline)


def test_missing_forbidden_finding_cannot_silently_clear_a_baseline(
    tmp_path: Path,
) -> None:
    _write(tmp_path, {"a.py": "import b\n", "b.py": ""})
    current = json.loads(
        render_json(analyse(tmp_path, forbidden_dependencies=(("a", "b"),)))
    )
    baseline = deepcopy(current)
    baseline["findings"] = []

    with pytest.raises(ValueError, match="forbidden dependencies do not match graph"):
        compare_baseline(current, baseline)


@pytest.mark.parametrize(
    "rules",
    [
        (("a", "b"), ("a", "b")),
        (("a", "b"), ("*", "b")),
        (("a", "b"), ("*", "b"), ("a", "b")),
    ],
)
def test_equivalent_forbidden_rules_have_identical_json_and_compatible_baselines(
    tmp_path: Path, rules: tuple[tuple[str, str], ...]
) -> None:
    _write(tmp_path, {"a.py": "import b\n", "b.py": ""})
    canonical = tuple(sorted(set(rules)))
    expected = render_json(analyse(tmp_path, forbidden_dependencies=canonical))
    actual = render_json(analyse(tmp_path, forbidden_dependencies=rules))

    assert actual == expected
    assert compare_baseline(json.loads(actual), json.loads(actual))["compatible"]
    assert compare_baseline(json.loads(actual), json.loads(expected))["compatible"]
    assert compare_baseline(json.loads(expected), json.loads(actual))["compatible"]


def test_standalone_findings_canonicalise_forbidden_rules(tmp_path: Path) -> None:
    _write(tmp_path, {"a.py": "import b\n", "b.py": ""})
    result = analyse(tmp_path)
    rules = (("a", "b"), ("*", "b"), ("a", "b"))

    findings = build_findings(result.architecture_dependencies, result.import_facts, rules)

    assert findings[0]["rules"] == [["*", "b"], ["a", "b"]]


def test_analyser_digest_detects_source_edits_without_version_or_commit_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "package" / "provenance.py"
    source.parent.mkdir()
    source.write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setattr(provenance, "__file__", str(source))
    before = provenance.analyser_identity()
    source.write_text("VALUE = 2\n", encoding="utf-8")

    after = provenance.analyser_identity()

    assert after["version"] == before["version"]
    assert after["commit"] == before["commit"]
    assert after["source_digest"] != before["source_digest"]
