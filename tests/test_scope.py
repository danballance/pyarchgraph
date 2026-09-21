from __future__ import annotations

from pathlib import Path

import pytest

from pyarchgraph.analysis import analyse
from pyarchgraph.findings import check_status
from pyarchgraph.policy import GraphPolicy


def _write(root: Path, files: dict[str, str]) -> None:
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def test_production_policy_excludes_test_modules_unless_requested(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        {
            "a.py": "import b\n",
            "b.py": "import a\n",
            "tests/test_a.py": "import a\n",
            "test_smoke.py": "import a\n",
            "smoke_test.py": "import a\n",
        },
    )

    production = analyse(tmp_path)
    with_tests = analyse(tmp_path, policy=GraphPolicy(include_tests=True))

    assert {module.id for module in production.modules} == {"a", "b"}
    assert {module.id for module in with_tests.modules} == {
        "a",
        "b",
        "tests.test_a",
        "test_smoke",
        "smoke_test",
    }
    assert production.quality.score == 0
    assert with_tests.quality.score > production.quality.score
    assert check_status(production) == check_status(with_tests) == "fail"


def test_wrong_src_layout_root_is_not_a_perfect_complete_score(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/a.py": "import pkg.b\n",
            "src/pkg/b.py": "import pkg.a\n",
        },
    )

    wrong = analyse(tmp_path)
    correct = analyse(
        tmp_path / "src", project_root=tmp_path, expected_packages=("pkg",)
    )

    assert wrong.complete
    assert not wrong.scope_valid
    assert wrong.quality.score is None
    assert check_status(wrong) == "needs_review"
    assert "source_root_mismatch" in {
        diagnostic.code for diagnostic in wrong.diagnostics
    }
    assert correct.complete and correct.scope_valid
    assert correct.quality.score == 0
    assert check_status(correct) == "fail"
    assert correct.provenance["source_root"] == "src"


def test_missing_expected_package_invalidates_scope_without_a_parse_failure(
    tmp_path: Path,
) -> None:
    _write(tmp_path, {"present.py": "VALUE = 1\n"})

    result = analyse(tmp_path, expected_packages=("expected",))

    assert result.complete
    assert not result.scope_valid
    assert result.quality.score is None
    assert check_status(result) == "needs_review"
    assert "expected_package_missing" in {
        diagnostic.code for diagnostic in result.diagnostics
    }


def test_namespace_base_does_not_have_missing_internal_target_semantics(
    tmp_path: Path,
) -> None:
    _write(tmp_path, {"app.py": "import ns\n", "ns/leaf.py": "VALUE = 1\n"})

    namespace = analyse(tmp_path)
    (tmp_path / "app.py").write_text("import ns.missing\n", encoding="utf-8")
    missing = analyse(tmp_path)

    assert namespace.complete and missing.complete
    assert namespace.unresolved_imports[0].reason.value == "namespace_base_unmodelled"
    assert namespace.dependency_resolution_complete
    assert check_status(namespace) == "pass"
    assert missing.unresolved_imports[0].reason.value == "missing_internal_target"
    assert not missing.dependency_resolution_complete
    assert check_status(missing) == "needs_review"


def test_project_root_must_contain_source_root(tmp_path: Path) -> None:
    _write(tmp_path, {"source/a.py": "", "other/placeholder.py": ""})

    with pytest.raises(ValueError, match="source_root must be inside project_root"):
        analyse(tmp_path / "source", project_root=tmp_path / "other")


def test_exact_evidence_keeps_mixed_probable_relationship_definite(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/a.py": "from pkg import b\nimport pkg.b\n",
            "pkg/b.py": "VALUE = 1\n",
        },
    )

    result = analyse(tmp_path, expected_packages=("pkg",))

    (edge,) = result.architecture_dependencies
    assert {(item.resolution_kind.value) for item in edge.evidence} == {
        "exact_module",
        "probable_submodule",
    }
    assert (
        result.complete and result.scope_valid and result.dependency_resolution_complete
    )
    assert check_status(result) == "pass"


def test_provenance_records_project_relative_root_and_effective_selection(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path, {"repository/src/pkg/__init__.py": "", "repository/src/pkg/a.py": ""}
    )
    project = tmp_path / "repository"

    result = analyse(
        project / "src",
        project_root=project,
        expected_packages=("pkg",),
        excludes=("generated",),
        policy=GraphPolicy(include_local=False),
    )

    provenance = result.provenance
    assert provenance["source_root"] == "src"
    assert not Path(provenance["source_root"]).is_absolute()
    assert provenance["expected_packages"] == ["pkg"]
    assert set(provenance["excludes"]) == {
        "generated",
        "tests",
        "test_*.py",
        "*_test.py",
    }
    assert provenance["graph_policy"] == {
        "include_type_only": True,
        "include_local": False,
        "include_tests": False,
    }
    assert provenance["python_version"] == result.python_version
    assert provenance["analyser"]["version"]
    assert "commit" in provenance["analyser"]
    assert len(provenance["analyser"]["source_digest"]) == 64
