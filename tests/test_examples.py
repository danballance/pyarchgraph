"""Acceptance scenarios are committed source data, never executable fixtures."""

from __future__ import annotations

import ast
import hashlib
import json
import subprocess
from functools import cache
from pathlib import Path

import networkx as nx
import pytest

from examples import evaluate
from pyarchgraph.discovery import discover_modules
from pyarchgraph.extraction import AstImportFactSource
from pyarchgraph.model import ImportSyntax, UnresolvedReason
from pyarchgraph.resolution import architecture_dependencies, resolve_imports

EXAMPLES = evaluate.EXAMPLES
MANIFEST = evaluate.load_manifest()
PROJECTS = {project["id"]: project for project in MANIFEST["projects"]}
RUNS = [(project["id"], variant_id) for project, variant_id, _ in evaluate.iter_runs()]
BASELINE = json.loads((EXAMPLES / "review-baseline.json").read_text(encoding="utf-8"))


def _configuration(project_id, variant_id=None):
    return next(
        config
        for _, variant, config in evaluate.iter_runs([project_id])
        if variant == variant_id
    )


@cache
def _completed(project_id, variant_id=None):
    return evaluate.run_cli(project_id, variant_id)


def _report(project_id, variant_id=None):
    return json.loads(_completed(project_id, variant_id).stdout)


@cache
def _structure(project_id, variant_id=None):
    """Inspect engine output without expanding the deliberately small public API."""
    config = _configuration(project_id, variant_id)
    root = EXAMPLES / "projects" / project_id / config["source_root"]
    discovery = discover_modules(root, excludes=tuple(config["exclusions"]))
    collection = AstImportFactSource().collect(root, discovery.modules)
    resolution = resolve_imports(
        collection.facts, discovery.modules, discovery.namespace_prefixes
    )
    return collection, resolution, architecture_dependencies(resolution.dependencies)


def _pairs(project_id, variant_id=None):
    return {
        (edge.source, edge.target) for edge in _structure(project_id, variant_id)[2]
    }


def _cycles(project_id, variant_id=None):
    return [
        finding
        for finding in _report(project_id, variant_id)["findings"]
        if finding["kind"] == "cycle"
    ]


def _assert_evidence(evidence, project_id):
    assert set(evidence) == {
        "path",
        "line",
        "column",
        "source_segment",
        "resolution_kind",
    }
    assert not Path(evidence["path"]).is_absolute()
    source_path = evaluate.REPOSITORY / evidence["path"]
    assert source_path.is_relative_to(EXAMPLES / "projects" / project_id)
    lines = source_path.read_text(encoding="utf-8").splitlines()
    assert type(evidence["line"]) is int and 1 <= evidence["line"] <= len(lines)
    assert type(evidence["column"]) is int and evidence["column"] >= 1
    if evidence["source_segment"] is not None:
        first_line = evidence["source_segment"].splitlines()[0]
        assert first_line in lines[evidence["line"] - 1]
    assert evidence["resolution_kind"] in {
        None,
        "exact_module",
        "exact_base",
        "probable_submodule",
    }


def _assert_finding_contract(finding, project_id):
    kind = finding["kind"]
    if kind in {"cycle", "forbidden_dependency"}:
        fields = {"kind", "certainty", "witness"}
        fields.update({"members", "definite_members"} if kind == "cycle" else {"rules"})
        assert set(finding) == fields
        assert finding["certainty"] in {"definite", "possible"}
        assert finding["witness"]
        for edge in finding["witness"]:
            assert set(edge) == {"source", "target", "evidence"}
            assert edge["evidence"]
            for evidence in edge["evidence"]:
                _assert_evidence(evidence, project_id)
    else:
        assert kind == "unresolved_import"
        assert set(finding) == {
            "kind",
            "source",
            "requested",
            "code",
            "message",
            "evidence",
        }
        assert finding["message"] and finding["source"] and finding["code"]
        assert finding["evidence"]
        for evidence in finding["evidence"]:
            _assert_evidence(evidence, project_id)


def test_manifest_preserves_every_project_and_run():
    assert MANIFEST["schema_version"] == 2
    assert len(PROJECTS) == 25
    assert len(RUNS) == 28
    assert set(PROJECTS) == {
        path.name for path in (EXAMPLES / "projects").iterdir() if path.is_dir()
    }
    assert set(RUNS) == {
        (item["project"], item["variant"]) for item in BASELINE["observations"]
    }
    outcomes = [config["expected"]["outcome"] for _, _, config in evaluate.iter_runs()]
    assert outcomes.count("pass") == 9
    assert outcomes.count("fail") == 18
    assert outcomes.count("error") == 1
    for project_id, variant_id in RUNS:
        config = _configuration(project_id, variant_id)
        assert config["source_root"]
        assert isinstance(config["exclusions"], list)
        assert isinstance(config["forbidden_dependencies"], list)
        assert (EXAMPLES / "projects" / project_id / "README.md").is_file()


def test_committed_sources_match_archival_hashes_and_parse_without_execution():
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
def test_real_cli_matches_every_manifest_run(project_id, variant_id):
    config = _configuration(project_id, variant_id)
    completed = _completed(project_id, variant_id)
    assert evaluate.check_result(completed, config["expected"]) == []
    if config["expected"]["outcome"] == "error":
        return
    for finding in _report(project_id, variant_id)["findings"]:
        _assert_finding_contract(finding, project_id)


@pytest.mark.parametrize(
    "project_id",
    [name for name, project in PROJECTS.items() if project.get("structural")],
)
def test_declared_structural_relationships(project_id):
    expected = PROJECTS[project_id]["structural"]
    pairs = _pairs(project_id)
    assert set(map(tuple, expected.get("required_dependencies", []))) <= pairs
    assert not set(map(tuple, expected.get("absent_dependencies", []))) & pairs


def test_padding_does_not_conceal_or_change_the_original_cycle():
    names = [
        "definite_cycle",
        "cycle_with_independent_pair",
        "cycle_with_49_pairs",
        "cycle_with_test_padding",
        "cycle_with_isolated_modules",
    ]
    for name in names:
        assert _completed(name).returncode == 1
        (finding,) = _cycles(name)
        assert finding["certainty"] == "definite"
        assert (
            finding["members"] == finding["definite_members"] == ["inventory", "orders"]
        )
    default = _report("cycle_with_test_padding")
    production = _report("cycle_with_test_padding", "production_only")
    assert default == production
    assert default["module_count"] == default["dependency_count"] == 2


def test_directional_rule_rejects_a_shortcut_in_an_acyclic_graph():
    assert _completed("layered_service").returncode == 0
    assert _completed("dense_ordered_dag").returncode == 1
    assert not _cycles("dense_ordered_dag")
    assert nx.is_directed_acyclic_graph(nx.DiGraph(_pairs("dense_ordered_dag")))
    (finding,) = _report("dense_ordered_dag")["findings"]
    assert finding["kind"] == "forbidden_dependency"
    assert finding["rules"] == [["presentation", "repository"]]


def test_submodule_spelling_preserves_architecture_and_original_syntax():
    relative = _structure("spelling_relative")
    absolute = _structure("spelling_absolute")
    assert (
        _pairs("spelling_relative")
        == _pairs("spelling_absolute")
        == {("pkg", "pkg.a"), ("pkg.a", "pkg.b")}
    )
    assert _report("spelling_relative") == _report("spelling_absolute")
    assert {fact.source_segment for fact in relative[0].facts} != {
        fact.source_segment for fact in absolute[0].facts
    }
    assert {(edge.source, edge.target) for edge in relative[1].dependencies} != {
        (edge.source, edge.target) for edge in absolute[1].dependencies
    }


def test_local_and_typing_only_imports_always_contribute_to_cycles():
    typing_collection = _structure("type_only_cycle")[0]
    assert {
        (fact.source, fact.base_module, fact.line)
        for fact in typing_collection.facts
        if fact.base_module in {"customers", "orders"}
    } == {("customers", "orders", 5), ("orders", "customers", 5)}
    local_collection = _structure("local_import_cycle")[0]
    assert {
        (fact.source, fact.base_module, fact.line) for fact in local_collection.facts
    } == {("cache", "report", 3), ("report", "cache", 5)}
    for name in ("type_only_cycle", "local_import_cycle"):
        assert _report(name)["dependency_count"] == 2
        assert len(_cycles(name)) == 1
        assert _completed(name).returncode == 1


def test_duplicate_import_sites_preserve_evidence_without_inflating_edge_count():
    report = _report("mixed_import_evidence")
    assert report["dependency_count"] == 2
    (cycle,) = report["findings"]
    service_edge = next(
        edge for edge in cycle["witness"] if edge["source"] == "service"
    )
    assert [item["line"] for item in service_edge["evidence"]] == [3, 5]
    collection, _, _ = _structure("mixed_import_evidence")
    assert {
        (fact.line, fact.source_segment)
        for fact in collection.facts
        if fact.source == "service" and fact.base_module == "model"
    } == {(3, "import model"), (5, "import model as annotation_model")}


@pytest.mark.parametrize(
    "project_id,variant_id",
    [
        (name, variant)
        for name, variant in RUNS
        if any(
            finding["kind"] == "cycle"
            for finding in _configuration(name, variant)["expected"].get("findings", [])
        )
    ],
)
def test_every_component_has_one_bounded_valid_cycle_witness(project_id, variant_id):
    pairs = _pairs(project_id, variant_id)
    components = [
        component
        for component in nx.strongly_connected_components(nx.DiGraph(pairs))
        if len(component) > 1 or (next(iter(component)), next(iter(component))) in pairs
    ]
    findings = _cycles(project_id, variant_id)
    assert len(findings) == len(components)
    assert {tuple(finding["members"]) for finding in findings} == {
        tuple(sorted(component)) for component in components
    }
    for finding in findings:
        witness = finding["witness"]
        assert 1 <= len(witness) <= len(finding["members"])
        assert len({edge["source"] for edge in witness}) == len(witness)
        assert set(finding["definite_members"]) <= set(finding["members"])
        for index, edge in enumerate(witness):
            assert (edge["source"], edge["target"]) in pairs
            assert edge["target"] == witness[(index + 1) % len(witness)]["source"]
    if project_id == "self_import":
        assert len(findings[0]["witness"]) == 1
    if project_id == "scc_not_simple_cycle":
        assert max(map(len, nx.simple_cycles(nx.DiGraph(pairs)))) == 2
        assert len(findings[0]["members"]) == 3


def test_missing_targets_are_distinct_from_valid_namespace_bases():
    namespace = _structure("valid_namespace_package")[1]
    assert not any(
        item.reason is UnresolvedReason.MISSING_INTERNAL_TARGET
        for item in namespace.unresolved_imports
    )
    assert _pairs("valid_namespace_package") == {
        ("plugins.cli", "plugins.readers.csv_reader")
    }
    assert _report("valid_namespace_package")["findings"] == []
    (missing,) = _report("missing_internal_target")["findings"]
    assert missing["code"] == "missing_internal_target"
    assert (
        missing["evidence"][0]["path"]
        == "examples/projects/missing_internal_target/catalog/api.py"
    )
    assert missing["evidence"][0]["line"] == 2


@pytest.mark.parametrize(
    "project_id,explicit_import_lines",
    [
        ("dynamic_literal", [2]),
        ("dynamic_aliases", [2, 3, 4]),
        ("dynamic_nonliteral", [2, 3]),
    ],
)
def test_dynamic_calls_add_no_dependencies_or_findings(project_id, explicit_import_lines):
    assert _report(project_id)["findings"] == []
    assert _completed(project_id).returncode == 0
    collection, resolution, _ = _structure(project_id)
    assert not collection.diagnostics
    assert [
        fact.line for fact in collection.facts if fact.source == "loader"
    ] == explicit_import_lines
    assert all(
        fact.syntax in {ImportSyntax.IMPORT, ImportSyntax.IMPORT_FROM}
        for fact in collection.facts
    )
    assert {item.requested for item in resolution.external_imports} == {"importlib"}
    expected_pairs = (
        {("plugin", "loader")} if project_id == "dynamic_literal" else set()
    )
    assert _pairs(project_id) == expected_pairs


def test_documentation_edits_preserve_semantic_findings_and_update_locations():
    before = _report("location_only_edit")
    after = _report("location_only_edit", "after_documentation_edit")
    assert _pairs("location_only_edit") == _pairs(
        "location_only_edit", "after_documentation_edit"
    )
    (old_cycle,) = before["findings"]
    (new_cycle,) = after["findings"]
    assert {key: value for key, value in old_cycle.items() if key != "witness"} == {
        key: value for key, value in new_cycle.items() if key != "witness"
    }
    for old_edge, new_edge in zip(old_cycle["witness"], new_cycle["witness"]):
        assert (old_edge["source"], old_edge["target"]) == (
            new_edge["source"],
            new_edge["target"],
        )
        for old_evidence, new_evidence in zip(
            old_edge["evidence"], new_edge["evidence"]
        ):
            assert new_evidence["path"] == old_evidence["path"].replace(
                "/before/", "/after/"
            )
            assert new_evidence["line"] == old_evidence["line"] + 2
            for field in ("column", "source_segment", "resolution_kind"):
                assert new_evidence[field] == old_evidence[field]


def test_cli_json_is_deterministic_across_independent_processes():
    first = _completed("dense_cyclic_component")
    second = evaluate.run_cli("dense_cyclic_component")
    assert (first.returncode, first.stdout, first.stderr) == (
        second.returncode,
        second.stdout,
        second.stderr,
    )


def test_evaluator_treats_an_expected_failure_as_success():
    expected = PROJECTS["definite_cycle"]["expected"]
    assert evaluate.check_result(_completed("definite_cycle"), expected) == []


def test_evaluator_rejects_changed_counts_certainty_and_extra_findings():
    completed = _completed("definite_cycle")
    expected = PROJECTS["definite_cycle"]["expected"]
    report = json.loads(completed.stdout)
    report["module_count"] += 1
    report["findings"][0]["certainty"] = "possible"
    altered = subprocess.CompletedProcess(
        completed.args, completed.returncode, json.dumps(report), ""
    )
    errors = evaluate.check_result(altered, expected)
    assert any("module_count" in error for error in errors)
    assert any("missing expected finding" in error for error in errors)
    assert any("unexpected findings" in error for error in errors)


def test_evaluator_returns_nonzero_for_a_mismatched_expectation(monkeypatch, capsys):
    monkeypatch.setattr(
        evaluate, "evaluate", lambda projects: {"matched": False, "results": []}
    )
    monkeypatch.setattr("sys.argv", ["examples.evaluate", "--json"])
    assert evaluate.main() == 1
    assert json.loads(capsys.readouterr().out)["matched"] is False
