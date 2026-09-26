import json
from pathlib import Path

from pyarchgraph import analyse, render_json


def test_json_contract_contains_only_counts_and_self_contained_findings(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("import b\n")
    (tmp_path / "b.py").write_text("import a\n")
    report = analyse(tmp_path, forbidden_dependencies=(("a", "b"),))
    rendered = render_json(report)
    document = json.loads(rendered)
    assert set(document) == {
        "schema_version",
        "module_count",
        "dependency_count",
        "findings",
    }
    assert document["schema_version"] == "0.5"
    assert document["module_count"] == document["dependency_count"] == 2
    assert len(document["findings"]) == 2
    cycle = next(f for f in document["findings"] if f["kind"] == "cycle")
    boundary = next(
        f for f in document["findings"] if f["kind"] == "forbidden_dependency"
    )
    assert set(cycle) == {"kind", "certainty", "members", "definite_members", "witness"}
    assert cycle["members"] == cycle["definite_members"] == ["a", "b"]
    assert set(boundary) == {"kind", "certainty", "rules", "witness"}
    assert boundary["rules"] == [["a", "b"]]
    for finding in document["findings"]:
        for edge in finding["witness"]:
            assert set(edge) == {"source", "target", "evidence"}
            (evidence,) = edge["evidence"]
            assert set(evidence) == {
                "path",
                "line",
                "column",
                "source_segment",
                "resolution_kind",
            }
            assert evidence == {
                "path": edge["source"] + ".py",
                "line": 1,
                "column": 1,
                "source_segment": "import " + edge["target"],
                "resolution_kind": "exact_module",
            }
    assert rendered.endswith("\n") and not rendered.endswith("\n\n")


def test_json_is_deterministic_for_reordered_rules(tmp_path: Path) -> None:
    (tmp_path / "b.py").write_text("import a\n")
    (tmp_path / "a.py").write_text("import b\nimport b\n")
    rules = (("a", "b"), ("*", "b"))
    before = render_json(analyse(tmp_path, forbidden_dependencies=rules))
    after = render_json(
        analyse(tmp_path, forbidden_dependencies=tuple(reversed(rules)))
    )
    assert before == after


def test_import_concerns_have_complete_source_evidence(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    source = tmp_path / "src"
    source.mkdir()
    (source / "a.py").write_text("from . import impossible\n__import__('external')\n")
    document = json.loads(render_json(analyse(source)))
    assert {f["kind"] for f in document["findings"]} == {
        "dynamic_import",
        "unresolved_import",
    }
    for finding in document["findings"]:
        assert set(finding) == {
            "kind",
            "source",
            "requested",
            "code",
            "message",
            "evidence",
        }
        assert finding["source"] == "a"
        assert finding["code"] and finding["message"]
        (evidence,) = finding["evidence"]
        assert evidence["path"] == "src/a.py"
        assert evidence["source_segment"]
        assert evidence["line"] in {1, 2}
        assert evidence["column"] == 1
