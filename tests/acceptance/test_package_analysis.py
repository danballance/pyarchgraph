"""Package views share source collection while reporting their own relationships."""

import json
from pathlib import Path

import pytest

from pyarchgraph.adapters.driving.cli.application import CliExitCodePolicy
from pyarchgraph.adapters.driving.cli.rendering import JsonReportRenderer
from pyarchgraph.application.requests import AnalysisOptions, AnalysisRequest
from pyarchgraph.domain.models import ResolutionKind
from pyarchgraph.main import ApplicationFactory


def write_sources(root, sources):
    for name, content in sources.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def analyse(root, *, depth=None, gate="structural", analyzer=None, roots=(".",)):
    analyzer = analyzer or ApplicationFactory().create_analyzer()
    return analyzer.analyse(
        AnalysisRequest(
            tuple(Path(item) for item in roots),
            AnalysisOptions(gate=gate, package_max_depth=depth),
            base_dir=root,
        )
    )


def pairs(view):
    return {(edge.source, edge.target) for edge in view.dependencies}


def test_package_cycles_are_distinct_from_module_cycles_and_keep_provenance(tmp_path):
    write_sources(
        tmp_path,
        {
            "one/__init__.py": "",
            "one/entry.py": "import two.leaf\nimport one.leaf\n",
            "one/leaf.py": "",
            "two/__init__.py": "",
            "two/entry.py": "import one.leaf\n",
            "two/leaf.py": "",
            "isolated/leaf.py": "",
        },
    )
    report = analyse(tmp_path)
    assert report.views["structural"].cyclic_node_count == 0
    assert CliExitCodePolicy().exit_code(report) == 0
    view = report.views["package-structural"]
    assert pairs(view) == {
        ("package:one", "package:two"),
        ("package:two", "package:one"),
    }
    assert view.cyclic_node_count == view.dependency_count == 2
    assert {node.id for node in view.nodes} == {
        "package:one",
        "package:two",
        "package:isolated",
    }
    assert view.enabled_check_ids == ("cycles", "unresolved-imports")
    for edge in view.dependencies:
        (evidence,) = edge.evidence
        assert evidence.line == evidence.column == 1
        assert evidence.source.endswith("/entry.py")
        assert evidence.target.endswith("/leaf.py")
        assert evidence.fact_id
        assert (
            (tmp_path / evidence.path).read_text().startswith(evidence.source_segment)
        )
    selected = analyse(tmp_path, gate="package-structural")
    assert CliExitCodePolicy().exit_code(selected) == 1


def test_depth_is_per_run_and_preserves_module_results(tmp_path):
    write_sources(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/core/__init__.py": "",
            "app/core/model.py": "",
            "app/adapters/__init__.py": "",
            "app/adapters/files/__init__.py": "",
            "app/adapters/files/reader.py": "import app.core.model\n",
        },
    )
    analyzer = ApplicationFactory().create_analyzer()
    immediate = analyse(tmp_path, analyzer=analyzer)
    capped = analyse(tmp_path, depth=2, analyzer=analyzer)
    shallow = analyse(tmp_path, depth=1, analyzer=analyzer)
    repeated = analyse(tmp_path, analyzer=analyzer)
    assert repeated == immediate
    for name in ("structural", "non-typing", "module-body"):
        assert immediate.views[name] == capped.views[name] == shallow.views[name]
    assert pairs(immediate.views["package-structural"]) == {
        ("package:app.adapters.files", "package:app.core")
    }
    assert pairs(capped.views["package-structural"]) == {
        ("package:app.adapters", "package:app.core")
    }
    assert shallow.views["package-structural"].dependencies == ()
    memberships = {
        node.id: node.members for node in immediate.views["package-structural"].nodes
    }
    assert memberships["package:app"] == ("source:app/__init__.py",)
    assert memberships["package:app.adapters"] == ("source:app/adapters/__init__.py",)
    assert len(
        {source for members in memberships.values() for source in members}
    ) == len(immediate.sources)
    assert analyse(tmp_path, depth=99).views == immediate.views


def test_package_filters_keep_matching_import_evidence(tmp_path):
    write_sources(
        tmp_path,
        {
            "one/a.py": (
                "from typing import TYPE_CHECKING\n"
                "if TYPE_CHECKING:\n    import two.b\n"
                "def load():\n    import three.c\n"
                "import four.d\n"
            ),
            "two/b.py": "",
            "three/c.py": "",
            "four/d.py": "",
        },
    )
    report = analyse(tmp_path)
    for name, targets, lines in (
        ("structural", {"two", "three", "four"}, {3, 5, 6}),
        ("non-typing", {"three", "four"}, {5, 6}),
        ("module-body", {"four"}, {6}),
    ):
        view = report.views["package-" + name]
        assert pairs(view) == {
            ("package:one", "package:" + target) for target in targets
        }
        assert {
            item.line for edge in view.dependencies for item in edge.evidence
        } == lines


def test_namespace_packages_and_bare_namespace_limitation(tmp_path):
    write_sources(
        tmp_path,
        {
            "app/main.py": "import plugins.readers.csv_reader\nimport plugins.readers\n",
            "plugins/readers/csv_reader.py": "",
        },
    )
    report = analyse(tmp_path)
    assert report.status == "complete"
    view = report.views["package-structural"]
    assert pairs(view) == {("package:app", "package:plugins.readers")}
    assert [item.line for edge in view.dependencies for item in edge.evidence] == [1]
    assert any(
        "namespace" in limitation.lower() for limitation in report.coverage.limitations
    )
    assert CliExitCodePolicy().exit_code(report) == 0


def test_namespace_members_from_multiple_roots_form_one_package(tmp_path):
    write_sources(
        tmp_path,
        {
            "first/plugins/a.py": "import plugins.b\nimport services.worker\n",
            "second/plugins/b.py": "",
            "second/services/worker.py": "",
        },
    )
    report = analyse(tmp_path, roots=("first", "second"))
    view = report.views["package-structural"]
    assert report.status == "complete"
    assert pairs(view) == {("package:plugins", "package:services")}
    plugins = next(node for node in view.nodes if node.id == "package:plugins")
    assert plugins.members == (
        "source:first/plugins/a.py",
        "source:second/plugins/b.py",
    )


def test_standalone_path_only_and_shadowed_sources_remain_individual_nodes(tmp_path):
    write_sources(
        tmp_path,
        {
            "main.py": "import pkg.leaf\n",
            "wireless-networks.py": "import pkg.leaf\n",
            "pkg.py": "import pkg.leaf\n",
            "pkg/__init__.py": "",
            "pkg/leaf.py": "",
        },
    )
    report = analyse(tmp_path)
    view = report.views["package-structural"]
    nodes = {node.id: node for node in view.nodes}
    assert set(nodes) == {
        "source:main.py",
        "source:wireless-networks.py",
        "source:pkg.py",
        "package:pkg",
    }
    assert nodes["source:pkg.py"].members == ("source:pkg.py",)
    assert pairs(view) == {
        ("source:main.py", "package:pkg"),
        ("source:wireless-networks.py", "package:pkg"),
        ("source:pkg.py", "package:pkg"),
    }


def test_package_cycles_recover_exact_base_certainty_without_changing_modules(tmp_path):
    write_sources(
        tmp_path,
        {
            "one/__init__.py": "",
            "one/a.py": "from two import b\n",
            "two/__init__.py": "",
            "two/b.py": "from one import a\n",
        },
    )
    report = analyse(tmp_path)
    assert report.views["structural"].findings[0].finding.certainty == "possible"
    package = report.views["package-structural"]
    assert package.findings[0].finding.certainty == "definite"
    for edge in package.dependencies:
        assert {item.resolution_kind for item in edge.evidence} == {
            ResolutionKind.EXACT_BASE,
            ResolutionKind.PROBABLE_SUBMODULE,
        }
        assert len({item.fact_id for item in edge.evidence}) == 1


def test_acyclic_dependencies_are_serialized_and_deterministic(tmp_path):
    write_sources(tmp_path, {"one/a.py": "import two.b\n", "two/b.py": ""})
    renderer = JsonReportRenderer()
    rendered = renderer.render(analyse(tmp_path))
    assert rendered == renderer.render(analyse(tmp_path))
    document = json.loads(rendered)
    assert document["schema_version"] == "0.8"
    view = document["views"]["package-structural"]
    assert view["findings"] == []
    (edge,) = view["dependencies"]
    assert edge["source"] == "package:one" and edge["target"] == "package:two"
    (evidence,) = edge["evidence"]
    assert (
        evidence["path"] == "one/a.py" and evidence["source_segment"] == "import two.b"
    )
    assert document["views"]["structural"]["dependencies"] is None
    assert set(edge) == {"source", "target", "evidence"}


def test_cli_depth_changes_package_gate_and_empty_dependency_output(tmp_path, capsys):
    write_sources(
        tmp_path,
        {
            "app/one/a.py": "import app.two.b\n",
            "app/two/b.py": "import app.one.a\n",
        },
    )
    cli = ApplicationFactory().create_cli()
    assert cli.run([str(tmp_path), "--gate", "package-structural"]) == 1
    assert (
        json.loads(capsys.readouterr().out)["views"]["package-structural"][
            "cyclic_node_count"
        ]
        == 2
    )
    assert (
        cli.run(
            [str(tmp_path), "--gate", "package-structural", "--package-max-depth", "1"]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["views"]["package-structural"]["dependencies"] == []
    assert report["views"]["structural"]["cyclic_node_count"] == 2


@pytest.mark.parametrize("depth", ["0", "-1"])
def test_cli_invalid_depth_precedes_discovery(tmp_path, capsys, depth):
    assert (
        ApplicationFactory()
        .create_cli()
        .run(
            [
                str(tmp_path / "absent"),
                "--package-max-depth",
                depth,
            ]
        )
        == 2
    )
    output = capsys.readouterr()
    assert output.out == "" and "package_max_depth" in output.err


def test_cli_noninteger_depth_is_an_invocation_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        ApplicationFactory().create_cli().run(
            [str(tmp_path), "--package-max-depth", "two"]
        )
    assert error.value.code == 2
    assert capsys.readouterr().out == ""


def test_package_gate_preserves_missing_imports_and_incomplete_coverage(tmp_path):
    write_sources(tmp_path, {"pkg/__init__.py": "", "pkg/a.py": "import pkg.missing\n"})
    report = analyse(tmp_path, gate="package-structural")
    assert CliExitCodePolicy().exit_code(report) == 1
    (finding,) = report.selected_view.findings
    assert (
        finding.finding.source == "source:pkg/a.py"
        and finding.finding.node == "package:pkg"
    )
    write_sources(tmp_path, {"pkg/broken.py": "def broken(:\n"})
    incomplete = analyse(tmp_path, gate="package-structural")
    assert (
        incomplete.status == "incomplete"
        and CliExitCodePolicy().exit_code(incomplete) == 2
    )
    assert incomplete.selected_view.findings
