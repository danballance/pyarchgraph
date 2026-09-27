"""Integration checks for scope interpretation across roots and declarations."""

from pathlib import Path

import pytest

from pyarchgraph import AnalysisOptions, TargetDeclaration, analyse, render_json


def _write(root: Path, files: dict[str, str]) -> None:
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


@pytest.mark.parametrize(
    "metadata",
    [
        ("pyproject.toml", '[tool.setuptools.package-dir]\npkg = "python_code/pkg"\n'),
        ("setup.cfg", "[options]\npackage_dir =\n    pkg = python_code/pkg\n"),
    ],
)
def test_named_package_directory_infers_the_parent_import_root(tmp_path, metadata):
    _write(
        tmp_path,
        {
            "app.py": "import pkg.api\n",
            "python_code/pkg/__init__.py": "",
            "python_code/pkg/api.py": "",
            metadata[0]: metadata[1],
        },
    )
    wrong = analyse((tmp_path,))
    assert wrong.status == "incomplete" and wrong.exit_code == 2
    errors = [
        item
        for item in wrong.coverage.diagnostics
        if item.code == "source_root_mismatch"
    ]
    assert len(errors) == 1
    assert "below python_code/" in errors[0].message
    corrected = analyse((tmp_path, tmp_path / "python_code"))
    assert corrected.status == "complete" and corrected.exit_code == 0
    assert corrected.selected_view.dependency_count == 1


def test_named_subpackage_mapping_strips_the_whole_package_suffix(tmp_path):
    _write(
        tmp_path,
        {
            "app.py": "import acme.service.api\n",
            "python_code/acme/service/__init__.py": "",
            "python_code/acme/service/api.py": "",
            "pyproject.toml": '[tool.setuptools.package-dir]\n"acme.service" = "python_code/acme/service"\n',
        },
    )
    report = analyse((tmp_path,))
    assert report.exit_code == 2
    assert any(
        item.code == "source_root_mismatch" and "below python_code/" in item.message
        for item in report.coverage.diagnostics
    )


@pytest.mark.parametrize(
    "metadata",
    [
        ("pyproject.toml", '[tool.setuptools.package-dir]\npkg = "implementation"\n'),
        ("setup.cfg", "[options]\npackage_dir =\n    pkg = implementation\n"),
    ],
)
def test_renamed_packaging_directory_is_an_explicit_coverage_error(tmp_path, metadata):
    _write(
        tmp_path,
        {
            "app.py": "import pkg.api\n",
            "implementation/__init__.py": "",
            "implementation/api.py": "",
            metadata[0]: metadata[1],
        },
    )
    report = analyse((tmp_path,))
    assert report.exit_code == 2
    assert any(
        item.code == "unsupported_package_mapping"
        for item in report.coverage.diagnostics
    )


def test_unused_or_excluded_package_alias_does_not_create_a_root_error(tmp_path):
    _write(
        tmp_path,
        {
            "app.py": "import unrelated\n",
            "implementation/__init__.py": "",
            "implementation/api.py": "",
            "pyproject.toml": '[tool.setuptools.package-dir]\npkg = "implementation"\n',
        },
    )
    assert analyse((tmp_path,)).exit_code == 0
    (tmp_path / "app.py").write_text("import pkg.api\n")
    excluded = analyse(
        (tmp_path,), options=AnalysisOptions(excludes=("implementation",))
    )
    assert excluded.exit_code == 0


def test_regular_package_cannot_absorb_another_roots_namespace_fragment(tmp_path):
    _write(
        tmp_path,
        {
            "one/app.py": "import pkg.child\nimport pkg.safe\n",
            "one/pkg/__init__.py": "",
            "one/pkg/safe.py": "",
            "two/pkg/child.py": "",
        },
    )
    roots = (tmp_path / "one", tmp_path / "two")
    report = analyse(roots)
    assert report.status == "incomplete" and report.exit_code == 2
    assert report.selected_view.dependency_count == 1
    assert any(
        item.code == "ambiguous_import_binding" for item in report.coverage.diagnostics
    )
    child = next(item for item in report.sources if item.import_name == "pkg.child")
    assert child.binding_status == "ambiguous"
    assert render_json(report) == render_json(analyse(tuple(reversed(roots))))


def test_pure_namespace_fragments_can_merge_across_roots(tmp_path):
    _write(
        tmp_path,
        {
            "one/ns/first.py": "import ns.second\n",
            "two/ns/second.py": "",
        },
    )
    report = analyse((tmp_path / "one", tmp_path / "two"))
    assert report.status == "complete" and report.exit_code == 0
    assert report.selected_view.dependency_count == 1


def test_regular_subpackage_only_blocks_its_own_foreign_namespace_descendants(tmp_path):
    _write(
        tmp_path,
        {
            "one/app.py": "import ns.regular.foreign\nimport ns.other\n",
            "one/ns/regular/__init__.py": "",
            "two/ns/regular/foreign.py": "",
            "two/ns/other.py": "",
        },
    )
    report = analyse((tmp_path / "one", tmp_path / "two"))
    assert report.exit_code == 2
    assert report.selected_view.dependency_count == 1
    assert (
        next(
            item for item in report.sources if item.import_name == "ns.other"
        ).binding_status
        == "bound"
    )


@pytest.mark.parametrize(
    "kind,filename,excludes",
    [
        ("native", "two/pkg/extension.pyx", ()),
        ("stub", "two/pkg/extension.pyi", ()),
        ("excluded", "two/pkg/extension/source.py", ("pkg/extension",)),
    ],
)
def test_acknowledged_foreign_target_cannot_bypass_regular_package_boundary(
    tmp_path, kind, filename, excludes
):
    _write(
        tmp_path,
        {
            "one/app.py": "import pkg.extension\n",
            "one/pkg/__init__.py": "",
            "two/helper.py": "",
            filename: "",
        },
    )
    declarations = (
        ()
        if kind == "excluded"
        else (
            TargetDeclaration(
                "pkg.extension",
                kind,
                "Accepted implementation boundary",
                acknowledged=True,
            ),
        )
    )
    report = analyse(
        (tmp_path / "one", tmp_path / "two"),
        options=AnalysisOptions(excludes=excludes, targets=declarations),
    )
    assert report.exit_code == 2
    assert report.coverage.boundaries[0].acknowledged
    assert any(
        item.code == "incompatible_target_layout"
        for item in report.coverage.diagnostics
    )


def test_declared_build_target_can_have_sources_outside_the_python_package(tmp_path):
    _write(tmp_path, {"pkg/__init__.py": "", "pkg/app.py": "import pkg.extension\n"})
    report = analyse(
        (tmp_path,),
        options=AnalysisOptions(
            targets=(
                TargetDeclaration(
                    "pkg.extension",
                    "native",
                    "Build mapping names the installed extension",
                    path="native_code/implementation.c",
                    acknowledged=True,
                ),
            )
        ),
    )
    assert report.exit_code == 0
    assert report.coverage.boundaries[0].kind == "native"


def test_generated_declaration_coexists_with_a_companion_stub(tmp_path):
    _write(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/app.py": "import pkg.generated\n",
            "pkg/generated.pyi": "",
        },
    )
    report = analyse(
        (tmp_path,),
        options=AnalysisOptions(
            targets=(
                TargetDeclaration(
                    "pkg.generated",
                    "generated",
                    "Generated at packaging time",
                    acknowledged=True,
                ),
            )
        ),
    )
    assert report.exit_code == 0
    (boundary,) = report.coverage.boundaries
    assert boundary.kind == "generated" and boundary.acknowledged


@pytest.mark.parametrize("kind", ["native", "generated", "excluded"])
def test_acknowledgement_cannot_create_children_below_a_nonpackage(tmp_path, kind):
    _write(tmp_path, {"app.py": "import plain.child\n", "plain.py": ""})
    excludes = ()
    declarations = ()
    if kind == "excluded":
        _write(tmp_path, {"plain/child/hidden.py": ""})
        excludes = ("plain/child",)
    else:
        declarations = (
            TargetDeclaration(
                "plain.child", kind, "Accepted boundary", acknowledged=True
            ),
        )
    report = analyse(
        (tmp_path,), options=AnalysisOptions(excludes=excludes, targets=declarations)
    )
    assert report.exit_code == 2
    assert report.coverage.boundaries[0].acknowledged
    assert any(
        item.code == "non_package_target_prefix" for item in report.coverage.diagnostics
    )
