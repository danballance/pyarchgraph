"""Modern type syntax is parsed without interpreting annotation expressions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from pyarchgraph import analyse
from pyarchgraph.cli import main
from pyarchgraph.discovery import discover_modules
from pyarchgraph.extraction import AstImportFactSource

pytestmark = pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 syntax")


def _sources(root: Path, declarations: str) -> None:
    (root / "a.py").write_text(
        "from base import BaseModel\n"
        "from importlib import import_module as load\n" + declarations,
        encoding="utf-8",
    )
    (root / "base.py").write_text("class BaseModel: pass\n", encoding="utf-8")
    (root / "b.py").write_text("import a\n", encoding="utf-8")


@pytest.mark.parametrize(
    "declaration",
    [
        "type Scalar = int\n",
        "type Value = int | str | None\n",
        "type Recursive = int | list[Recursive]\n",
        "type Forward = Later\nclass Later: pass\n",
        "type Generic[T] = list[T]\n",
        "type Variadic[*Ts] = tuple[*Ts]\n",
        "type CallableAlias[**P] = Callable[P, int]\n",
        "type Bounded[T: BaseModel, U: (int, str)] = tuple[T, U]\n",
        "def function():\n    type Local[T] = list[T]\n",
        "class Container:\n    type Local[T] = list[T]\n",
        "def validate[ModelT: BaseModel](value: ModelT) -> ModelT:\n    return value\n",
        "async def validate[ModelT: BaseModel](value: ModelT) -> ModelT:\n    return value\n",
        "class Container[ModelT: BaseModel]:\n    value: ModelT\n",
        'type Alias = load("b")\n',
        'type Alias[T: load("b")] = T\n',
        'type Alias[T: (int, __import__("b"))] = T\n',
        'type Alias = (lambda: load("b"))\n',
        'class Container:\n    type Alias = load("b")\n',
        'def validate[T: load("b")]():\n    pass\n',
        'class Container[T: load("b")]:\n    pass\n',
    ],
)
def test_type_declarations_leave_only_explicit_import_dependencies(
    tmp_path: Path, declaration: str
) -> None:
    _sources(tmp_path, declaration)

    result = AstImportFactSource().collect(tmp_path, discover_modules(tmp_path).modules)

    assert result.diagnostics == ()
    assert [(fact.source, fact.base_module) for fact in result.facts] == [
        ("a", "base"),
        ("a", "importlib"),
        ("b", "a"),
    ]
    report = analyse(tmp_path, forbidden_dependencies=(("a", "b"),))
    assert report.dependency_count == 2
    assert report.findings == ()


@pytest.mark.skipif(sys.version_info < (3, 13), reason="PEP 696 syntax")
@pytest.mark.parametrize(
    "parameters",
    [
        "T = BaseModel",
        "T: BaseModel = BaseModel",
        "*Ts = *tuple[str, ...]",
        "**P = [int, str]",
        'T = load("b")',
        '*Ts = *load("b")',
        '**P = __import__("b")',
    ],
)
@pytest.mark.parametrize(
    "definition",
    [
        "type Alias[{parameters}] = int\n",
        "def function[{parameters}]():\n    pass\n",
        "class Container[{parameters}]:\n    pass\n",
    ],
)
def test_type_parameter_defaults_are_not_interpreted(
    tmp_path: Path, definition: str, parameters: str
) -> None:
    _sources(tmp_path, definition.format(parameters=parameters))

    result = AstImportFactSource().collect(tmp_path, discover_modules(tmp_path).modules)

    assert result.diagnostics == ()
    assert [(fact.source, fact.base_module) for fact in result.facts] == [
        ("a", "base"),
        ("a", "importlib"),
        ("b", "a"),
    ]
    assert analyse(tmp_path).findings == ()


def test_generic_bodies_keep_explicit_imports(tmp_path: Path) -> None:
    _sources(
        tmp_path,
        "def validate[ModelT: BaseModel](value: ModelT) -> ModelT:\n"
        "    import b\n"
        "    return value\n",
    )

    report = analyse(tmp_path)

    (cycle,) = report.findings
    assert cycle.kind == "cycle"
    assert cycle.certainty == "definite"
    assert cycle.members == ("a", "b")


def test_cli_does_not_evaluate_type_alias_expressions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _sources(
        tmp_path,
        'def explode():\n    raise RuntimeError("source executed")\n'
        'type Alias = explode()\ntype DynamicAlias = load("b")\n',
    )

    assert main([str(tmp_path)]) == 0
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert captured.err == ""
    assert document["module_count"] == 3
    assert document["dependency_count"] == 2
    assert document["findings"] == []
