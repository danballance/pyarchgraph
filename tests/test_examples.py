"""Acceptance corpus: source text is data, and fixture applications never run.

Historical observations document reviewed defects; desired contracts exercise
current behavior. Formula controls deliberately retain the documented heuristic
limitations, so score dilution is never mistaken for resolution improvement.
"""

from __future__ import annotations

import ast
from functools import lru_cache
import hashlib
import json
from pathlib import Path

import networkx as nx
import pytest

from pyarchgraph import analyse, render_json
from pyarchgraph.findings import check_status
from pyarchgraph.model import ImportSyntax
from pyarchgraph.policy import GraphPolicy
from pyarchgraph.provenance import compare_baseline


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
MANIFEST = json.loads((EXAMPLES / "manifest.json").read_text(encoding="utf-8"))
PROJECTS = {project["id"]: project for project in MANIFEST["projects"]}
BASELINE = json.loads((EXAMPLES / "review-baseline.json").read_text(encoding="utf-8"))
RUNS = [
    (project["id"], variant["id"] if variant else None)
    for project in MANIFEST["projects"]
    for variant in [None, *project.get("variants", [])]
]


def _configuration(project_id, variant_id=None):
    project = PROJECTS[project_id]
    if variant_id is None:
        return project
    return next(
        variant for variant in project["variants"] if variant["id"] == variant_id
    )


@lru_cache(maxsize=None)
def _analyse(project_id, variant_id=None):
    config = _configuration(project_id, variant_id)
    project_dir = EXAMPLES / "projects" / project_id
    desired = config["desired"]
    rules = desired.get("forbidden_dependencies", []) + desired.get(
        "directional_violations", []
    )
    return analyse(
        project_dir / config["source_root"],
        project_root=project_dir,
        excludes=tuple(config["exclusions"]),
        expected_packages=tuple(config["expected_packages"]),
        policy=GraphPolicy(**config["graph_policy"]),
        forbidden_dependencies=tuple(tuple(rule) for rule in rules),
    )


def _pairs(result):
    return {(edge.source, edge.target) for edge in result.architecture_dependencies}


def _cycles(result, certainty=None):
    return [
        finding
        for finding in result.findings
        if finding["kind"] == "cycle"
        and (certainty is None or finding["certainty"] == certainty)
    ]


def test_manifest_and_historical_evidence_are_complete():
    assert MANIFEST["schema_version"] == 1
    assert len(PROJECTS) >= 25
    assert set(PROJECTS) == {
        path.name for path in (EXAMPLES / "projects").iterdir() if path.is_dir()
    }
    assert BASELINE["analyser_commit"] == "925276dbedbc324427a619aab91a35a719e8e183"
    assert BASELINE["formula_version"] == "architecture-v1"
    assert BASELINE["analyser_file_sha256"]
    assert BASELINE["python_version"]
    observations = {
        (item["project"], item["variant"]): item for item in BASELINE["observations"]
    }
    assert set(observations) == set(RUNS)
    for project_id, variant_id in RUNS:
        config = _configuration(project_id, variant_id)
        assert config["source_root"]
        assert isinstance(config["exclusions"], list)
        assert isinstance(config["expected_packages"], list)
        assert config["desired"]
        assert (
            config["observed_reviewed"]["score"]
            == observations[(project_id, variant_id)]["score"]
        )
        assert (EXAMPLES / "projects" / project_id / "README.md").is_file()


def test_committed_sources_match_reviewed_evidence_and_parse_without_execution():
    files = sorted((EXAMPLES / "projects").rglob("*.py"))
    assert len(files) >= 269
    assert {path.relative_to(EXAMPLES).as_posix() for path in files} == set(
        BASELINE["source_sha256"]
    )
    for path in files:
        source = path.read_bytes()
        assert (
            hashlib.sha256(source).hexdigest()
            == BASELINE["source_sha256"][path.relative_to(EXAMPLES).as_posix()]
        )
        ast.parse(source, filename=str(path))


@pytest.mark.parametrize(
    "project_id,variant_id",
    RUNS,
    ids=[f"{name}/{variant or 'default'}" for name, variant in RUNS],
)
def test_desired_structural_outcomes(project_id, variant_id):
    config = _configuration(project_id, variant_id)
    desired = config["desired"]
    result = _analyse(project_id, variant_id)
    assert result.complete is desired.get("complete", True)
    assert result.scope_valid is desired.get("scope_valid", True)
    for metric in (
        "cyclic_module_count",
        "dependency_count",
        "reachable_pair_count",
        "active_module_count",
        "isolated_module_count",
        "largest_cyclic_component_size",
    ):
        if metric in desired:
            assert getattr(result.quality.metrics, metric) == desired[metric]
    if "score" in desired:
        assert result.quality.score == pytest.approx(desired["score"])
    if not result.scope_valid:
        assert result.quality.score is None
    assert set(map(tuple, desired.get("required_dependencies", []))) <= _pairs(result)
    assert not (
        set(map(tuple, desired.get("forbidden_dependencies", []))) & _pairs(result)
    )
    for key, certainty in (
        ("definite_cycle_count", "definite"),
        ("candidate_cycle_count", "possible"),
    ):
        if key in desired:
            assert len(_cycles(result, certainty)) == desired[key]
    if desired.get("uncertainty_must_be_reported"):
        assert not result.dependency_resolution_complete
        assert check_status(result) == "needs_review"
    if desired.get("must_not_claim_safe"):
        assert check_status(result) != "pass"
    reasons = {item.reason.value for item in result.unresolved_imports}
    assert set(desired.get("unresolved_reasons", [])) <= reasons
    assert not (set(desired.get("forbidden_unresolved_reasons", [])) & reasons)
    if "directional_violations" in desired:
        forbidden = {
            tuple((item["source"], item["target"]))
            for finding in result.findings
            if finding["kind"] == "forbidden_dependency"
            for item in finding["witness"]
        }
        assert forbidden == set(map(tuple, desired["directional_violations"]))
    if "dynamic_calls" in desired:
        assert result.quality.dynamic_import_warning_count == desired["dynamic_calls"]
        assert not result.dependency_resolution_complete
        literal_targets = {
            fact.base_module
            for fact in result.import_facts
            if fact.syntax is ImportSyntax.DYNAMIC_IMPORT
        }
        assert literal_targets == set(desired.get("literal_dynamic_targets", []))


def test_historical_failures_are_evidence_not_current_requirements():
    old = {
        (item["project"], item["variant"]): item for item in BASELINE["observations"]
    }
    assert old[("spelling_relative", None)]["score"] == pytest.approx(100 / 3)
    assert old[("spelling_absolute", None)]["score"] == 85
    assert old[("src_root_hazard", "incorrect_repository_root")]["complete"]
    assert old[("src_root_hazard", "incorrect_repository_root")]["score"] == 100
    assert old[("dynamic_aliases", None)]["dynamic_import_warning_count"] == 0
    assert (
        _analyse("spelling_relative").quality.score
        == _analyse("spelling_absolute").quality.score
    )
    assert (
        _analyse("src_root_hazard", "incorrect_repository_root").quality.score is None
    )
    assert _analyse("dynamic_aliases").quality.dynamic_import_warning_count > 0


def test_formula_dilution_never_clears_the_unchanged_definite_cycle():
    project_ids = [
        "definite_cycle",
        "cycle_with_independent_pair",
        "cycle_with_49_pairs",
        "cycle_with_test_padding",
    ]
    results = [_analyse(name) for name in project_ids]
    assert [result.quality.score for result in results] == pytest.approx(
        [0, 57.5, 98.44545454545455, 98.03921568627452]
    )
    identities = set()
    for result in results:
        assert check_status(result) == "fail"
        (finding,) = _cycles(result, "definite")
        assert set(finding["members"]) == {"orders", "inventory"}
        identities.add(finding["id"])
    assert len(identities) == 1
    assert _analyse("cycle_with_test_padding", "production_only").quality.score == 0
    assert _analyse("cycle_with_isolated_modules").quality.score == 0


def test_formula_reachability_blind_spot_requires_a_directional_rule():
    chain = _analyse("layered_service")
    dense = _analyse("dense_ordered_dag")
    assert chain.quality.score == dense.quality.score == 85
    assert (
        chain.quality.metrics.reachable_pair_count
        == dense.quality.metrics.reachable_pair_count
        == 6
    )
    assert chain.quality.metrics.dependency_count == 3
    assert dense.quality.metrics.dependency_count == 6
    assert check_status(chain) == "pass"
    assert check_status(dense) == "fail"


def test_submodule_spelling_preserves_the_same_architecture_and_raw_evidence():
    relative = _analyse("spelling_relative")
    absolute = _analyse("spelling_absolute")
    assert (
        _pairs(relative) == _pairs(absolute) == {("pkg", "pkg.a"), ("pkg.a", "pkg.b")}
    )
    assert relative.quality.score == absolute.quality.score == 85
    assert not _cycles(relative)
    assert not _cycles(absolute)
    assert {(edge.source, edge.target) for edge in relative.dependencies} != {
        (edge.source, edge.target) for edge in absolute.dependencies
    }
    assert {fact.source_segment for fact in relative.import_facts} != {
        fact.source_segment for fact in absolute.import_facts
    }


@pytest.mark.parametrize(
    "project_id,policy",
    [
        ("type_only_cycle", GraphPolicy(include_type_only=False)),
        ("local_import_cycle", GraphPolicy(include_local=False)),
        ("mixed_import_evidence", GraphPolicy(include_type_only=False)),
    ],
)
def test_evidence_filters_keep_edges_with_any_remaining_support(project_id, policy):
    project = PROJECTS[project_id]
    root = EXAMPLES / "projects" / project_id
    result = analyse(root, policy=policy)
    prefix = "without_type_only" if not policy.include_type_only else "without_local"
    assert (
        result.quality.metrics.dependency_count
        == project["desired"][f"{prefix}_dependency_count"]
    )
    assert (
        result.quality.metrics.cyclic_module_count
        == project["desired"][f"{prefix}_cyclic_module_count"]
    )
    if project_id == "mixed_import_evidence":
        assert check_status(result) == "fail"
        facts = {fact.id: fact for fact in result.import_facts}
        assert all(
            not facts[evidence.fact_id].type_only
            for edge in result.architecture_dependencies
            for evidence in edge.evidence
        )


@pytest.mark.parametrize(
    "project_id",
    [name for name in PROJECTS if "bounded_witness_count" in PROJECTS[name]["desired"]]
    + ["self_import"],
)
def test_component_findings_have_bounded_valid_witnesses_with_source_locations(
    project_id,
):
    result = _analyse(project_id)
    findings = _cycles(result)
    assert len(findings) == PROJECTS[project_id]["desired"].get(
        "bounded_witness_count", 1
    )
    source_root = (
        EXAMPLES / "projects" / project_id / PROJECTS[project_id]["source_root"]
    )
    for finding in findings:
        witness = finding["witness"]
        assert 1 <= len(witness) <= len(finding["members"])
        assert witness[-1]["target"] == witness[0]["source"]
        for index, edge in enumerate(witness):
            assert edge["target"] == witness[(index + 1) % len(witness)]["source"]
            assert edge["evidence"]
            for evidence in edge["evidence"]:
                lines = (
                    (source_root / evidence["path"])
                    .read_text(encoding="utf-8")
                    .splitlines()
                )
                assert evidence["line"] >= 1
                assert evidence["source_segment"] in lines[evidence["line"] - 1]
    if project_id == "scc_not_simple_cycle":
        graph = nx.DiGraph(_pairs(result))
        assert max(map(len, nx.simple_cycles(graph))) == 2
        assert result.quality.metrics.largest_cyclic_component_size == 3


def test_missing_targets_and_namespace_bases_have_distinct_resolution_status():
    missing = _analyse("missing_internal_target")
    namespace = _analyse("valid_namespace_package")
    assert missing.complete and namespace.complete
    assert not missing.dependency_resolution_complete
    assert "missing_internal_target" not in {
        item.reason.value for item in namespace.unresolved_imports
    }
    # Namespace bases may remain as explanatory unresolved records. A definite
    # child import still supports the relationship when its candidate duplicate
    # is filtered; no missing source file should be invented.
    assert _pairs(namespace) == {("plugins.cli", "plugins.readers.csv_reader")}


def test_location_edits_preserve_violation_and_dependency_identity():
    project_dir = EXAMPLES / "projects" / "location_only_edit"
    # Treat before/after as two checkouts of the same configured import root.
    before = analyse(project_dir / "before", project_root=project_dir / "before")
    after = analyse(project_dir / "after", project_root=project_dir / "after")
    assert _pairs(before) == _pairs(after)
    assert {fact.id for fact in before.import_facts} != {
        fact.id for fact in after.import_facts
    }
    assert [item["id"] for item in before.findings] == [
        item["id"] for item in after.findings
    ]
    old_document, new_document = (
        json.loads(render_json(before)),
        json.loads(render_json(after)),
    )
    comparison = compare_baseline(new_document, old_document)
    assert comparison["added_dependencies"] == []
    assert comparison["removed_dependencies"] == []
    assert comparison["new_definite_cyclic_dependencies"] == []
