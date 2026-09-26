"""All import concerns are reported even in local and typing-only scopes."""

from pathlib import Path

import pytest

from pyarchgraph import analyse


@pytest.mark.parametrize("guard", ["if TYPE_CHECKING:", "def later():"])
def test_local_and_typing_import_concerns_always_block(
    tmp_path: Path, guard: str
) -> None:
    statement = "import pkg.missing"
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
    assert finding.kind == "unresolved_import"
    assert finding.source == "pkg.a"
    assert finding.requested == "pkg.missing"
    assert finding.evidence[0].line == 5
    assert finding.evidence[0].source_segment == statement
    assert report.dependency_count == 1


@pytest.mark.parametrize("guard", ["if TYPE_CHECKING:", "def later():", "class Container:"])
@pytest.mark.parametrize(
    "call",
    [
        "import_module('pkg.b')",
        "import_module('pkg.missing')",
        "import_module(target)",
        "__import__('pkg.b')",
        "__import__('pkg.missing')",
        "__import__('external')",
    ],
)
def test_dynamic_calls_produce_no_dependency_or_finding(
    tmp_path: Path, guard: str, call: str
) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "b.py").write_text("import pkg.a\n")
    (package / "a.py").write_text(
        "from typing import TYPE_CHECKING\nfrom importlib import import_module\n"
        + guard
        + "\n    "
        + call
        + "\n"
    )
    report = analyse(tmp_path, forbidden_dependencies=(("pkg.a", "pkg.b"),))
    assert report.findings == ()
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
