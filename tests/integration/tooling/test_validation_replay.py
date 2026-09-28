"""The independent replay checker must reject corrupted or stale results."""

import copy
import json
from types import SimpleNamespace

import pytest

from benchmarks import replay_validation as replay
from benchmarks.replay_validation import source_sites


def test_independent_evidence_audit_uses_character_columns_and_lexical_ancestry(
    tmp_path,
):
    path = tmp_path / "source.py"
    path.write_text(
        "é = 1; import target\ndef f():\n    class C:\n        import nested\n"
    )
    sites = source_sites(path)
    assert sites[1, 8] == {
        "source_segment": "import target",
        "scope": "module",
        "in_function": False,
        "exception_handler": False,
    }
    assert sites[4, 9]["scope"] == "class"
    assert sites[4, 9]["in_function"]


@pytest.fixture
def cycle_result(tmp_path, monkeypatch):
    monkeypatch.setattr(replay, "ARCHIVE", tmp_path / "archive")
    sources, witness = [], []
    for source, target in (("a", "b"), ("b", "a")):
        path = source + ".py"
        snippet = "import " + target
        (tmp_path / path).write_text(snippet + "\n")
        sources.append(
            {
                "id": source,
                "path": path,
                "import_name": source,
                "binding_status": "bound",
            }
        )
        witness.append(
            {
                "source": source,
                "target": target,
                "evidence": [
                    {
                        "path": path,
                        "line": 1,
                        "column": 1,
                        "source_segment": snippet,
                        "resolution_kind": "exact_module",
                        "context": {
                            "scope": "module",
                            "in_function": False,
                            "typing_only": False,
                            "exception_handler": False,
                            "conditional": False,
                            "package_initializer": False,
                        },
                    }
                ],
            }
        )
    view = {
        "dependency_count": 2,
        "cyclic_node_count": 2,
        "cyclic_dependency_count": 2,
        "findings": [
            {
                "kind": "cycle",
                "members": ["a", "b"],
                "definite_members": ["a", "b"],
                "certainty": "definite",
                "dependency_count": 2,
                "witness": witness,
                "dependencies": None,
            }
        ],
    }
    view["findings"] = [
        {"check_id": "cycles", "severity": "error", "finding": finding}
        for finding in view["findings"]
    ]
    view["nodes"] = [
        {"id": name, "label": name, "members": [name]} for name in ("a", "b")
    ]
    view["enabled_check_ids"] = ["cycles", "unresolved-imports"]
    return {"cwd": str(tmp_path), "original_case": "test"}, {
        "exit_code": 1,
        "stderr": "",
        "report": {
            "sources": sources,
            "gate": "structural",
            "status": "complete",
            "coverage": {"boundaries": []},
            "views": {
                name: copy.deepcopy(view)
                for name in ("structural", "non-typing", "module-body")
            },
        },
        "graphs": {
            name: [["a", "b"], ["b", "a"]]
            for name in ("structural", "non-typing", "module-body")
        },
    }


@pytest.mark.parametrize(
    "corruption,expected_error",
    [
        ("empty_evidence", "empty evidence list"),
        ("invented_edge", "witness edge absent from graph"),
        ("definite_members", "definite members exceed component"),
        ("cyclic_count", "cyclic dependency count mismatch"),
        ("component_count", "component dependency count mismatch"),
        ("foreign_evidence", "evidence path does not belong to dependency source"),
        ("typing_in_filtered_view", "typing-only evidence survived filter"),
        ("missing_component_edge", "component detail does not cover its graph edges"),
    ],
)
def test_report_audit_rejects_corrupted_cycle_results(
    cycle_result, corruption, expected_error
):
    case, result = cycle_result
    assert not replay.audit_report(case, result)["errors"]
    view = result["report"]["views"]["non-typing"]
    finding = view["findings"][0]["finding"]
    edge = finding["witness"][0]
    if corruption == "empty_evidence":
        edge["evidence"] = []
    elif corruption == "invented_edge":
        edge["target"] = edge["source"]
        finding["witness"] = [edge]
    elif corruption == "definite_members":
        finding["definite_members"].append("unknown")
    elif corruption == "cyclic_count":
        view["cyclic_dependency_count"] += 1
    elif corruption == "component_count":
        finding["dependency_count"] += 1
    elif corruption == "foreign_evidence":
        edge["evidence"] = finding["witness"][1]["evidence"]
    elif corruption == "typing_in_filtered_view":
        edge["evidence"][0]["context"]["typing_only"] = True
    elif corruption == "missing_component_edge":
        finding["dependencies"] = [edge]
    assert any(
        expected_error in error for error in replay.audit_report(case, result)["errors"]
    )


@pytest.mark.parametrize(
    "corruption",
    [None, "empty", "missing", "extra", "duplicate", "kind", "path", "unacknowledged"],
)
def test_boundary_comparison_requires_exact_declared_targets(tmp_path, corruption):
    declared = [
        {
            "name": name,
            "kind": "native",
            "path": str(tmp_path / (name + ".pyx")),
            "acknowledged": True,
        }
        for name in ("a", "b")
    ]
    boundaries = [dict(item, path=item["name"] + ".pyx") for item in declared]
    report = {"status": "complete", "coverage": {"boundaries": boundaries}}
    if corruption == "empty":
        boundaries.clear()
    elif corruption == "missing":
        boundaries.pop()
    elif corruption == "extra":
        boundaries.append(dict(boundaries[0], name="unexpected"))
    elif corruption == "duplicate":
        boundaries.append(boundaries[0].copy())
    elif corruption == "kind":
        boundaries[0]["kind"] = "stub"
    elif corruption == "path":
        boundaries[0]["path"] = "different.pyx"
    elif corruption == "unacknowledged":
        boundaries[0]["acknowledged"] = False
    assert replay.boundary_declarations_match(
        {"cwd": str(tmp_path), "targets": declared}, report
    ) is (corruption is None)


def test_failed_replay_removes_previous_success_artifacts(tmp_path, monkeypatch):
    case = {"name": "failed"}
    for suffix in ("report.json", "graph.json", "profile.json", "run.json"):
        (tmp_path / ("failed." + suffix)).write_text("stale data")
    monkeypatch.setattr(
        replay.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stderr="worker failed"),
    )
    record = replay.replay_case(case, tmp_path)
    assert "worker failed" in record["exception"]
    assert json.loads((tmp_path / "failed.run.json").read_text()) == record
    assert not any(
        (tmp_path / ("failed." + suffix)).exists()
        for suffix in ("report.json", "graph.json", "profile.json")
    )
    # Even stale payloads copied back by another process cannot count as fresh
    # results when this invocation's run record failed.
    (tmp_path / "sympy.graph.json").write_text("must not be read")
    assert replay.expected_deltas(tmp_path, [record]) is False
    assert (
        json.loads((tmp_path / "expected-deltas.json").read_text())["all_passed"]
        is False
    )


@pytest.fixture
def replay_environment(tmp_path, monkeypatch):
    archive = tmp_path / "archive"
    archive.mkdir()
    output = tmp_path / "output"
    for name in (
        "pandas",
        "comfyui",
        "comfyui-supported-subset",
        "comprebuddy-standalone-root-diagnostic",
    ):
        replay.write_json(
            archive / (name + ".current.run.json"),
            {
                "case": name,
                "cwd": str(tmp_path),
                "source_root": ".",
                "excludes": [],
                "exit_code": 1,
                "repository_commit": "archived-provenance",
            },
        )
    monkeypatch.setattr(replay, "ARCHIVE", archive)
    monkeypatch.setattr(replay, "archival_hashes", lambda: {"archive": "unchanged"})
    monkeypatch.setattr(replay, "targets_for", lambda *args, **kwargs: [])
    return output


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "audit",
        "missing_edges",
        "source_changed",
        "exception",
        "expected_deltas",
        "delta_exception",
        "archive_changed",
    ],
)
def test_replay_exit_reports_validation_failures(
    replay_environment, monkeypatch, failure
):
    output = replay_environment
    record = {
        "audit": {"errors": [], "missing_restricted_independent_edges": []},
        "analyzer_source_sha256": {"analyzer.py": "unchanged"},
        "analyzer_source_unchanged_during_run": True,
    }
    if failure == "audit":
        record["audit"]["errors"] = ["invalid witness"]
    elif failure == "missing_edges":
        record["audit"]["missing_restricted_independent_edges"] = [["a", "b"]]
    elif failure == "source_changed":
        record["analyzer_source_unchanged_during_run"] = False
    elif failure == "exception":
        record = {"exception": "worker failed"}
    elif failure == "archive_changed":
        hashes = iter(({"archive": "before"}, {"archive": "after"}))
        monkeypatch.setattr(replay, "archival_hashes", lambda: next(hashes))
    monkeypatch.setattr(
        replay, "replay_case", lambda case, output: dict(record, case=case)
    )

    def deltas(output, records):
        if failure == "delta_exception":
            raise ValueError("missing current payload")
        return failure != "expected_deltas"

    monkeypatch.setattr(replay, "expected_deltas", deltas)
    monkeypatch.setattr(replay, "profile_comparison", lambda output: None)
    monkeypatch.setattr(replay.sys, "argv", ["replay", "--output", str(output)])
    assert replay.main() == (0 if failure is None else 1)
    summary = json.loads((output / "replay-summary.json").read_text())
    assert summary["all_passed"] is (failure is None)


def test_selected_replay_removes_stale_full_corpus_deltas(
    replay_environment, monkeypatch
):
    output = replay_environment
    output.mkdir()
    (output / "expected-deltas.json").write_text('{"all_passed": true}')
    monkeypatch.setattr(
        replay,
        "replay_case",
        lambda case, output: {"case": case, "exception": "worker failed"},
    )
    monkeypatch.setattr(
        replay.sys, "argv", ["replay", "--output", str(output), "--case", "pandas"]
    )
    assert replay.main() == 1
    assert not (output / "expected-deltas.json").exists()
    summary = json.loads((output / "replay-summary.json").read_text())
    assert summary["expected_deltas_passed"] is None


def test_unknown_replay_case_is_an_error(replay_environment, monkeypatch):
    monkeypatch.setattr(
        replay.sys,
        "argv",
        ["replay", "--output", str(replay_environment), "--case", "typo"],
    )
    with pytest.raises(SystemExit) as error:
        replay.main()
    assert error.value.code == 2
    assert not replay_environment.exists()


def test_profile_comparison_identifies_source_cache_constructor(tmp_path, monkeypatch):
    from pyarchgraph.adapters.driven.python_ast import _SourceText

    monkeypatch.setattr(replay, "ARCHIVE", tmp_path / "archive")
    replay.ARCHIVE.mkdir()
    (replay.ARCHIVE / "sympy.current.profile.json").write_text(
        json.dumps(
            {
                "elapsed_seconds_with_profiling": 20,
                "top_functions": [
                    {"function": "collect", "cumulative_seconds": 10},
                    {
                        "function": "get_source_segment",
                        "calls": 20,
                        "cumulative_seconds": 5,
                    },
                ],
            }
        )
    )
    (tmp_path / "sympy.profile.json").write_text(
        json.dumps(
            {
                "total_seconds": 2,
                "extraction_functions": [
                    {"function": "collect", "cumulative_seconds": 1},
                    {
                        "function": "segment",
                        "total_calls": 10,
                        "cumulative_seconds": 0.2,
                    },
                    {"function": "__init__", "line": 1, "cumulative_seconds": 0.001},
                    {
                        "function": "__init__",
                        "line": _SourceText.__init__.__code__.co_firstlineno,
                        "cumulative_seconds": 0.5,
                    },
                ],
            }
        )
    )
    saved = {}
    monkeypatch.setattr(replay, "write_json", lambda path, value: saved.update(value))
    replay.profile_comparison(tmp_path)
    assert saved["current"]["source_cache_cumulative_seconds"] == 0.5


@pytest.fixture
def package_worker_result(tmp_path):
    files = {
        "app/one/a.py": (
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n    import app.two.x\n"
            "def load():\n    import app.three.y\n"
            "import app.four.z\n"
        ),
        "app/one/leaf.py": "",
        "app/two/b.py": "import app.one.leaf\n",
        "app/two/x.py": "",
        "app/three/c.py": "import app.one.leaf\n",
        "app/three/y.py": "",
        "app/four/d.py": "import app.one.leaf\n",
        "app/four/z.py": "",
    }
    for name, text in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    case = {
        "name": "package-roundtrip",
        "original_case": "package-roundtrip",
        "cwd": str(tmp_path),
        "roots": ["."],
        "excludes": [],
        "supplement": True,
    }
    completed = replay.subprocess.run(
        [replay.sys.executable, "-I", replay.__file__, "--worker"],
        input=json.dumps(case),
        text=True,
        capture_output=True,
        check=True,
        cwd=replay.PROJECT,
    )
    assert completed.stderr == ""
    return case, json.loads(completed.stdout)


def test_worker_roundtrip_audits_six_views_and_package_contexts(package_worker_result):
    case, result = package_worker_result
    assert result["report"]["schema_version"] == "0.8"
    assert result["exit_code"] == 0
    assert set(result["graphs"]) == set(result["report"]["views"])
    assert len(result["graphs"]) == 6
    assert all(
        not replay.components(result["graphs"][name])
        for name in ("structural", "non-typing", "module-body")
    )
    for name, expected in (
        ("package-structural", 4),
        ("package-non-typing", 3),
        ("package-module-body", 2),
    ):
        assert result["report"]["views"][name]["cyclic_node_count"] == expected
        assert result["graph_observation_sources"][name] == "reported_dependencies"
    assert result["graph_observation_sources"]["structural"] == "analysis_snapshot"
    audit = replay.audit_report(case, result)
    assert audit["errors"] == []
    assert audit["evidence_checked"] > 0
    assert "not an independent package edge oracle" in audit["package_graph_limit"]


@pytest.mark.parametrize(
    "corruption,expected_error",
    [
        ("source", "evidence source does not belong to dependency node"),
        ("target", "evidence target does not belong to dependency node"),
        ("missing_edge", "full dependencies do not cover its graph edges"),
        ("missing_dependencies", "full package dependencies are missing"),
        ("typing_context", "typing-only evidence survived filter"),
        ("deferred_context", "deferred evidence survived filter"),
    ],
)
def test_package_report_audit_rejects_corrupted_relationships(
    package_worker_result, corruption, expected_error
):
    case, result = package_worker_result
    view = result["report"]["views"]["package-module-body"]
    edge = view["dependencies"][0]
    evidence = edge["evidence"][0]
    if corruption == "source":
        evidence["source"] = evidence["target"]
    elif corruption == "target":
        evidence["target"] = evidence["source"]
    elif corruption == "missing_edge":
        view["dependencies"].pop()
    elif corruption == "missing_dependencies":
        view["dependencies"] = None
    elif corruption == "typing_context":
        evidence["context"]["typing_only"] = True
    elif corruption == "deferred_context":
        evidence["context"]["in_function"] = True
    assert any(
        expected_error in error for error in replay.audit_report(case, result)["errors"]
    )


def test_replay_case_roundtrip_saves_package_graph_provenance(
    package_worker_result, tmp_path
):
    case, _ = package_worker_result
    case = dict(case, package_max_depth=1)
    output = tmp_path / "fresh-replay"
    output.mkdir()
    record = replay.replay_case(case, output)
    assert record["audit"]["errors"] == []
    report = json.loads((output / "package-roundtrip.report.json").read_text())
    graph = json.loads((output / "package-roundtrip.graph.json").read_text())
    assert report["schema_version"] == "0.8"
    assert report["views"]["package-structural"]["dependencies"] == []
    assert len(report["views"]["package-structural"]["nodes"]) == 1
    assert (
        graph["graph_observation_sources"]["package-structural"]
        == "reported_dependencies"
    )
    assert graph["graph_observation_sources"]["structural"] == "analysis_snapshot"


@pytest.mark.parametrize("directory", ["0.7", "2026-09-26"])
def test_replay_refuses_to_overwrite_archived_validation(directory, monkeypatch):
    destination = replay.PROJECT / "docs/validation" / directory
    monkeypatch.setattr(replay.sys, "argv", ["replay", "--output", str(destination)])
    with pytest.raises(SystemExit) as error:
        replay.main()
    assert error.value.code == 2


def test_replay_defaults_to_fresh_schema_output():
    assert replay.OUTPUT == replay.PROJECT / "docs/validation/0.8"
