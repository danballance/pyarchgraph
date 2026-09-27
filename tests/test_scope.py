from pathlib import Path

import pytest

from pyarchgraph import (
    AnalysisError,
    AnalysisOptions,
    TargetDeclaration,
    analyse,
    render_json,
)


def _write(root: Path, files: dict[str, str]) -> None:
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def test_tests_and_generated_directories_are_excluded_and_disclosed(tmp_path):
    _write(
        tmp_path,
        {
            "a.py": "import b",
            "b.py": "import a",
            "tests/test_a.py": "syntax ! error",
            "test_smoke.py": "bad !",
            ".venv/bad.py": "bad !",
            "build/bad.py": "bad !",
        },
    )
    report = analyse((tmp_path,))
    assert report.status == "complete" and report.exit_code == 1
    assert len(report.sources) == report.selected_view.dependency_count == 2
    assert {
        item.path.rsplit("/", 1)[-1] for item in report.coverage.excluded_paths
    } >= {"tests", ".venv", "build", "test_smoke.py"}


@pytest.mark.parametrize(
    "folder,metadata",
    [
        ("src", ""),
        ("lib", ""),
        ("python_code", '[tool.setuptools.packages.find]\nwhere = ["python_code"]\n'),
    ],
)
def test_wrong_root_is_incomplete_and_correct_root_retains_cycle(
    tmp_path, folder, metadata
):
    _write(
        tmp_path,
        {
            f"{folder}/pkg/__init__.py": "",
            f"{folder}/pkg/a.py": "import pkg.b",
            f"{folder}/pkg/b.py": "import pkg.a",
        },
    )
    if metadata:
        (tmp_path / "pyproject.toml").write_text(metadata)
    wrong = analyse((tmp_path,))
    assert wrong.exit_code == 2
    assert "source_root_mismatch" in {item.code for item in wrong.coverage.diagnostics}
    right = analyse((tmp_path / folder,))
    assert right.exit_code == 1 and right.status == "complete"
    assert right.selected_view.dependency_count == 2


def test_vendored_suffix_is_advisory_not_a_root_error(tmp_path):
    _write(tmp_path, {"app.py": "import thirdparty", "vendor/thirdparty.py": ""})
    report = analyse((tmp_path,))
    assert report.exit_code == 0
    assert [item.code for item in report.coverage.diagnostics] == [
        "possible_source_root"
    ]


def test_namespace_siblings_are_external_unless_ownership_declared(tmp_path):
    _write(tmp_path, {"app.py": "import ns.missing", "ns/leaf.py": ""})
    assert analyse((tmp_path,)).exit_code == 0
    strict = analyse((tmp_path,), options=AnalysisOptions(owned_prefixes=("ns",)))
    assert strict.exit_code == 1
    assert strict.selected_view.findings[0].requested == "ns.missing"


def test_explicit_exclusions_remove_source_but_preserve_scope(tmp_path):
    _write(
        tmp_path,
        {"kept.py": "", "generated/bad.py": "invalid !", "bad.py": "invalid !"},
    )
    report = analyse(
        (tmp_path,), options=AnalysisOptions(excludes=("generated", "bad.py"))
    )
    assert len(report.sources) == 1 and report.exit_code == 0
    assert len(report.coverage.excluded_paths) == 2


def test_partial_findings_survive_syntax_failure(tmp_path):
    _write(
        tmp_path, {"a.py": "import b", "b.py": "import a", "broken.py": "def nope(:"}
    )
    report = analyse((tmp_path,))
    assert report.exit_code == 2 and report.status == "incomplete"
    assert len(report.selected_view.findings) == 1
    assert report.coverage.analyzed_source_count == 2
    assert (
        next(
            source for source in report.sources if source.import_name == "broken"
        ).analysis_status
        == "error"
    )


def test_broken_source_remains_a_known_dependency_target(tmp_path):
    _write(tmp_path, {"a.py": "import broken", "broken.py": "def nope(:"})
    report = analyse((tmp_path,))
    assert report.exit_code == 2 and report.selected_view.dependency_count == 1
    assert report.selected_view.findings == ()


def test_empty_root_returns_incomplete_report(tmp_path):
    report = analyse((tmp_path,))
    assert report.exit_code == 2 and report.sources == ()
    assert report.coverage.diagnostics[0].code == "no_sources"


def test_invalid_root_is_configuration_error(tmp_path):
    with pytest.raises(AnalysisError):
        analyse((tmp_path / "missing",))
    with pytest.raises(AnalysisError):
        analyse(())
    with pytest.raises(AnalysisError):
        analyse(tmp_path)


@pytest.mark.parametrize("excludes", [("/absolute",), ("",)])
def test_invalid_exclusion_is_rejected(tmp_path, excludes):
    with pytest.raises(ValueError):
        analyse((tmp_path,), options=AnalysisOptions(excludes=excludes))


def test_nested_roots_retain_entrypoint_edges_without_duplicate_sources(tmp_path):
    _write(
        tmp_path,
        {
            "main.py": "import pkg.api",
            "src/pkg/__init__.py": "",
            "src/pkg/api.py": "",
            "other/src/keep.py": "",
        },
    )
    report = analyse((tmp_path, tmp_path / "src"))
    assert report.exit_code == 0
    assert len(report.sources) == 4 and report.selected_view.dependency_count == 1
    assert {source.import_name for source in report.sources} == {
        "main",
        "pkg",
        "pkg.api",
        "other.src.keep",
    }
    assert render_json(report) == render_json(
        analyse((tmp_path / "src", tmp_path, tmp_path))
    )


def test_cross_root_duplicate_bindings_are_incomplete(tmp_path):
    _write(
        tmp_path,
        {"one/app.py": "import shared", "one/shared.py": "", "two/shared.py": ""},
    )
    report = analyse((tmp_path / "one", tmp_path / "two"))
    assert report.exit_code == 2
    assert sum(source.binding_status == "ambiguous" for source in report.sources) == 2
    assert report.selected_view.dependency_count == 0
    assert "ambiguous_import_binding" in {
        item.code for item in report.coverage.diagnostics
    }


@pytest.mark.parametrize("gate", ["structural", "non-typing", "module-body"])
def test_implementation_boundaries_are_independent_of_gate(tmp_path, gate):
    _write(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/app.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import pkg.native",
        },
    )
    (tmp_path / "pkg/native.pyx").write_text("cdef int value")
    report = analyse((tmp_path,), options=AnalysisOptions(gate=gate))
    assert report.exit_code == 2
    assert len(report.coverage.boundaries) == 1
    accepted = analyse(
        (tmp_path,),
        options=AnalysisOptions(
            gate=gate,
            targets=(
                TargetDeclaration(
                    "pkg.native", "native", "Compiled separately", acknowledged=True
                ),
            ),
        ),
    )
    assert accepted.exit_code == 0
    assert accepted.coverage.boundaries[0].acknowledged
    assert accepted.coverage.analyzed_source_count == 2


def test_acknowledgement_cannot_hide_parse_failure_or_other_boundary(tmp_path):
    _write(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/app.py": "import pkg.one\nimport pkg.two",
            "bad.py": "bad !",
        },
    )
    for name in ("one", "two"):
        (tmp_path / f"pkg/{name}.pyi").write_text("value: int")
    report = analyse(
        (tmp_path,),
        options=AnalysisOptions(
            targets=(
                TargetDeclaration(
                    "pkg.one", "stub", "Accepted type interface", acknowledged=True
                ),
            )
        ),
    )
    assert report.exit_code == 2
    assert [item.acknowledged for item in report.coverage.boundaries] == [True, False]


def test_source_backed_target_is_not_suppressed_by_declaration(tmp_path):
    _write(tmp_path, {"a.py": "import b", "b.py": "import a"})
    report = analyse(
        (tmp_path,),
        options=AnalysisOptions(
            targets=(
                TargetDeclaration(
                    "b", "generated", "Old generated target", acknowledged=True
                ),
            )
        ),
    )
    assert report.exit_code == 1 and report.selected_view.dependency_count == 2
    assert not report.coverage.boundaries
    assert "unused_acknowledgement" in {
        item.code for item in report.coverage.diagnostics
    }


def test_same_root_package_precedence_preserves_shadowed_source(tmp_path):
    _write(
        tmp_path,
        {
            "app.py": "import pkg",
            "pkg.py": "import app",
            "pkg/__init__.py": "",
            "pkg/child.py": "",
        },
    )
    report = analyse((tmp_path,))
    assert report.exit_code == 0 and len(report.sources) == 4
    assert report.selected_view.dependency_count == 2
    assert (
        next(
            source for source in report.sources if source.path.endswith("/pkg.py")
        ).binding_status
        == "shadowed"
    )


def test_unusual_files_are_analyzed_without_executing_them(tmp_path):
    _write(
        tmp_path,
        {
            "wireless-networks.py": "import target",
            "is/0001_start.py": "import target",
            ".hidden/helper.py": "import target",
            "target.py": "",
        },
    )
    report = analyse((tmp_path,))
    assert report.exit_code == 0 and len(report.sources) == 4
    assert report.selected_view.dependency_count == 3
    assert (
        next(
            source for source in report.sources if ".hidden/" in source.path
        ).import_name
        is None
    )


def test_native_acknowledgement_coalesces_companion_stub(tmp_path):
    _write(tmp_path, {"pkg/__init__.py": "", "pkg/app.py": "import pkg.engine"})
    (tmp_path / "pkg/engine.pyx").write_text("cdef int value")
    (tmp_path / "pkg/engine.pyi").write_text("value: int")
    report = analyse(
        (tmp_path,),
        options=AnalysisOptions(
            targets=(
                TargetDeclaration(
                    "pkg.engine", "native", "Compiled separately", acknowledged=True
                ),
            )
        ),
    )
    assert report.exit_code == 0
    (boundary,) = report.coverage.boundaries
    assert boundary.kind == "native" and boundary.acknowledged
