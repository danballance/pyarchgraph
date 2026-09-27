import json

from pyarchgraph import analyse, render_json


def test_json_contract_has_coverage_three_views_and_stable_source_references(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "a.py").write_text("import b\n")
    (tmp_path / "b.py").write_text("import a\n")
    rendered = render_json(analyse((tmp_path,)))
    document = json.loads(rendered)
    assert set(document) == {
        "schema_version",
        "status",
        "gate",
        "sources",
        "coverage",
        "views",
    }
    assert document["schema_version"] == "0.6"
    assert document["status"] == "complete" and document["gate"] == "structural"
    assert set(document["views"]) == {"structural", "non_typing", "module_body"}
    sources = {item["id"]: item for item in document["sources"]}
    assert set(sources) == {"source:a.py", "source:b.py"}
    assert all(item["analysis_status"] == "analyzed" for item in sources.values())
    for view in document["views"].values():
        assert set(view) == {
            "dependency_count",
            "cyclic_source_count",
            "cyclic_dependency_count",
            "findings",
        }
        assert (
            view["dependency_count"]
            == view["cyclic_source_count"]
            == view["cyclic_dependency_count"]
            == 2
        )
        (cycle,) = view["findings"]
        assert set(cycle) == {
            "kind",
            "certainty",
            "members",
            "definite_members",
            "witness",
            "dependency_count",
            "dependencies",
        }
        assert (
            cycle["members"]
            == cycle["definite_members"]
            == ["source:a.py", "source:b.py"]
        )
        assert cycle["dependencies"] is None
        for edge in cycle["witness"]:
            (evidence,) = edge["evidence"]
            assert set(evidence) == {
                "path",
                "line",
                "column",
                "source_segment",
                "resolution_kind",
                "context",
            }
            assert evidence["path"] == sources[edge["source"]]["path"]
            assert evidence["line"] == evidence["column"] == 1
            assert (
                evidence["source_segment"]
                == "import " + sources[edge["target"]]["import_name"]
            )
            assert evidence["context"] == {
                "scope": "module",
                "in_function": False,
                "typing_only": False,
                "conditional": False,
                "exception_handler": False,
                "package_initializer": False,
            }
    assert rendered.endswith("\n") and not rendered.endswith("\n\n")


def test_import_concerns_keep_source_root_prefix_and_evidence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_text("from . import impossible\n")
    document = json.loads(render_json(analyse((tmp_path / "src",))))
    (finding,) = document["views"]["structural"]["findings"]
    assert finding["source"] == "source:src/a.py"
    assert finding["kind"] == "unresolved_import"
    (evidence,) = finding["evidence"]
    assert (
        evidence["path"] == "src/a.py" and evidence["line"] == evidence["column"] == 1
    )
    assert evidence["source_segment"] == "from . import impossible"
