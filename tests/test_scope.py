from pathlib import Path

import pytest

from pyarchgraph import AnalysisError, analyse


def _write(root: Path, files: dict[str, str]) -> None:
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def test_test_and_generated_modules_are_always_excluded(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "a.py": "import b\n",
            "b.py": "import a\n",
            "tests/test_a.py": "syntax ! error",
            "test_smoke.py": "syntax ! error",
            "smoke_test.py": "syntax ! error",
            "nested/tests/helper.py": "syntax ! error",
            ".venv/bad.py": "syntax ! error",
            "build/bad.py": "syntax ! error",
        },
    )
    report = analyse(tmp_path)
    assert report.module_count == report.dependency_count == 2
    assert [f.kind for f in report.findings] == ["cycle"]


def test_wrong_src_layout_root_is_an_error_without_automatic_discovery(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        {
            "src/pkg/__init__.py": "",
            "src/pkg/a.py": "import pkg.b\n",
            "src/pkg/b.py": "import pkg.a\n",
        },
    )
    with pytest.raises(AnalysisError, match="source_root_mismatch"):
        analyse(tmp_path)
    assert [f.kind for f in analyse(tmp_path / "src").findings] == ["cycle"]


def test_namespace_base_is_benign_but_missing_child_blocks(tmp_path: Path) -> None:
    _write(tmp_path, {"app.py": "import ns\n", "ns/leaf.py": "VALUE = 1\n"})
    report = analyse(tmp_path)
    assert report.module_count == 2
    assert report.findings == ()
    (tmp_path / "app.py").write_text("import ns.missing\n")
    (finding,) = analyse(tmp_path).findings
    assert finding.kind == "unresolved_import"
    assert finding.requested == "ns.missing"
    assert finding.code == "missing_internal_target"


def test_explicit_excludes_remove_source_from_analysis(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "kept.py": "",
            "generated/bad.py": "invalid ! syntax",
            "bad.py": "invalid ! syntax",
        },
    )
    report = analyse(tmp_path, excludes=("generated", "bad.py"))
    assert report.module_count == 1
    assert report.findings == ()


@pytest.mark.parametrize("relative", ["", "__init__.py", "a.py"])
def test_empty_missing_or_package_root_is_invalid(
    tmp_path: Path, relative: str
) -> None:
    if relative:
        (tmp_path / relative).write_text("")
    root = tmp_path if relative != "a.py" else tmp_path / "missing"
    with pytest.raises(AnalysisError):
        analyse(root)


def test_syntax_failure_takes_precedence_over_existing_cycle(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {"a.py": "import b\n", "b.py": "import a\n", "broken.py": "def nope(:\n"},
    )
    with pytest.raises(AnalysisError, match="source_syntax_error"):
        analyse(tmp_path)


@pytest.mark.parametrize("excludes", [("/absolute",), ("",)])
def test_invalid_exclusion_is_rejected(
    tmp_path: Path, excludes: tuple[str, ...]
) -> None:
    _write(tmp_path, {"a.py": ""})
    with pytest.raises(ValueError):
        analyse(tmp_path, excludes=excludes)


def test_removed_forbidden_dependencies_keyword_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, {"a.py": ""})
    with pytest.raises(
        TypeError, match="unexpected keyword argument 'forbidden_dependencies'"
    ):
        analyse(tmp_path, forbidden_dependencies=(("a", "b"),))
