"""All import concerns are reported even in local and typing-only scopes."""

from pathlib import Path

import pytest

from pyarchgraph import analyse


@pytest.mark.parametrize("guard", ["if TYPE_CHECKING:", "def later():"])
@pytest.mark.parametrize(
    "statement,kind,requested",
    [
        ("import pkg.missing", "unresolved_import", "pkg.missing"),
        ("import_module('pkg.b')", "dynamic_import", "pkg.b"),
        ("import_module(target)", "dynamic_import", None),
        ("__import__('external')", "dynamic_import", "external"),
    ],
)
def test_local_and_typing_import_concerns_always_block(
    tmp_path: Path, guard: str, statement: str, kind: str, requested: str | None
) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "b.py").write_text("")
    (package / "a.py").write_text(
        "from typing import TYPE_CHECKING\nfrom importlib import import_module\n"
        + guard
        + "\n    import pkg.b\n    "
        + statement
        + "\n"
    )
    report = analyse(tmp_path)
    (finding,) = report.findings
    assert finding.kind == kind
    assert finding.source == "pkg.a"
    assert finding.requested == requested
    assert finding.evidence[0].line == 5
    assert finding.evidence[0].source_segment == statement
    assert report.dependency_count == 1


def test_relative_escape_is_an_explanatory_finding(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("from . import outside\n")
    (finding,) = analyse(tmp_path).findings
    assert finding.kind == "unresolved_import"
    assert finding.message
    assert finding.evidence[0].source_segment == "from . import outside"


def test_external_imports_are_benign(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        "import external\nfrom other_external import thing\n"
    )
    report = analyse(tmp_path)
    assert report.dependency_count == 0
    assert report.findings == ()
