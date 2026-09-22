"""PEP 695/696 aliases retain import evidence without evaluating types."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import textwrap

import pytest

from pyarchgraph import GraphPolicy, analyse
from pyarchgraph.cli import main
from pyarchgraph.findings import check_status
from pyarchgraph.model import ImportScope, ImportSyntax


pytestmark = pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 syntax")
LOADER = "from importlib import import_module as load\n"


def _cycle(tmp_path: Path, source: str):
    (tmp_path / "a.py").write_text(textwrap.dedent(source), encoding="utf-8")
    (tmp_path / "b.py").write_text("import a\n", encoding="utf-8")
    return analyse(tmp_path, policy=GraphPolicy(include_type_only=False))


def _dynamic_facts(result):
    return [f for f in result.import_facts if f.syntax is ImportSyntax.DYNAMIC_IMPORT]


@pytest.mark.parametrize(
    "source",
    [
        "type Scalar = int\n",
        "type Value = int | str | None\n",
        "type Collection = dict[str, list[int]]\n",
        "type Recursive = int | list[Recursive]\n",
        "type Forward = Later\nclass Later:\n    pass\n",
        "type Generic[T] = list[T]\n",
        "type Variadic[*Ts] = tuple[*Ts]\n",
        "type CallableAlias[**P] = Callable[P, int]\n",
        "type Bounded[T: int, U: (int, str)] = tuple[T, U]\n",
        "def function():\n    type Local[T] = list[T]\n",
        "class Container:\n    type Local[T] = list[T]\n",
    ],
)
def test_plain_aliases_are_complete_without_new_import_edges(
    tmp_path: Path, source: str
) -> None:
    result = _cycle(tmp_path, source)
    assert result.complete
    assert result.dependency_resolution_complete
    assert result.quality.score is not None
    assert not result.diagnostics
    assert check_status(result) == "pass"
    assert [(edge.source, edge.target) for edge in result.architecture_dependencies] == [
        ("b", "a")
    ]


@pytest.mark.skipif(sys.version_info < (3, 13), reason="PEP 696 syntax")
def test_alias_parameter_defaults_are_supported(tmp_path: Path) -> None:
    result = _cycle(
        tmp_path,
        "type Defaults[T = int, *Ts = *tuple[str, ...], **P = [int, str]] = tuple[T, *Ts]\n",
    )
    assert result.complete
    assert result.dependency_resolution_complete
    assert not result.diagnostics


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(LOADER + 'type Alias = load("b")\n', id="earlier-loader"),
        pytest.param('type Alias = load("b")\n' + LOADER, id="later-loader"),
        pytest.param(
            LOADER + 'type Alias = load("b")\nload = object()\n',
            id="later-rebinding",
        ),
        pytest.param(
            'type Alias = __import__("b")\n__import__ = object()\n',
            id="later-builtin-shadow",
        ),
        pytest.param(
            'def outer():\n    type Alias = load("b")\n    ' + LOADER,
            id="later-enclosing-local",
        ),
        pytest.param(
            'def setup():\n    global load\n    '
            + LOADER
            + 'type Alias = load("b")\n',
            id="global-loader-mutation",
        ),
        pytest.param(
            'def outer():\n    load = object()\n'
            '    def setup():\n        nonlocal load\n        '
            + LOADER
            + '    type Alias = load("b")\n',
            id="nonlocal-loader-mutation",
        ),
        pytest.param(
            'class Container:\n    type Alias = load("b")\n    ' + LOADER,
            id="later-class-loader",
        ),
        pytest.param(
            LOADER + 'type Alias[T: load("b")] = T\n', id="parameter-bound"
        ),
        pytest.param(
            LOADER + 'type Alias[T: (int, load("b"))] = T\n',
            id="parameter-constraint",
        ),
    ],
)
def test_lazy_alias_expressions_retain_possible_loaders(
    tmp_path: Path, source: str
) -> None:
    result = _cycle(tmp_path, source)
    facts = _dynamic_facts(result)
    assert len(facts) == 1
    assert facts[0].base_module == "b"
    assert not facts[0].type_only
    assert result.complete
    assert result.quality.score is not None
    assert not result.dependency_resolution_complete
    assert check_status(result) == "needs_review"
    assert {(edge.source, edge.target) for edge in result.architecture_dependencies} == {
        ("a", "b"),
        ("b", "a"),
    }
    assert result.findings[0]["certainty"] == "possible"
    assert [diagnostic.code for diagnostic in result.diagnostics] == [
        "dynamic_import_ignored"
    ]
    assert result.diagnostics[0].line == facts[0].line


@pytest.mark.skipif(sys.version_info < (3, 13), reason="PEP 696 syntax")
@pytest.mark.parametrize(
    "parameters",
    ['T = load("b")', '*Ts = *load("b")', '**P = load("b")'],
)
def test_alias_parameter_defaults_retain_loader_calls(
    tmp_path: Path, parameters: str
) -> None:
    result = _cycle(tmp_path, f'type Alias[{parameters}] = int\n' + LOADER)
    assert result.complete
    assert [fact.base_module for fact in _dynamic_facts(result)] == ["b"]
    assert check_status(result) == "needs_review"


@pytest.mark.parametrize(
    "source",
    [
        LOADER + 'type Alias[load] = load("b")\nload("b")\n',
        'def outer():\n    '
        + LOADER
        + '    type Alias[load] = load("b")\n    load("b")\n',
        'class Container:\n    '
        + LOADER
        + '    type Alias[load] = load("b")\n    load("b")\n',
    ],
)
def test_alias_parameters_do_not_escape_into_the_containing_scope(
    tmp_path: Path, source: str
) -> None:
    result = _cycle(tmp_path, source)
    facts = _dynamic_facts(result)
    assert result.complete
    assert len(facts) == 1
    assert facts[0].line == len(source.splitlines())


@pytest.mark.parametrize(
    ("source", "type_only"),
    [
        ("type Alias[TC] = int\ndef consumer():\n    if TC:\n        import b\n", True),
        ("def consumer():\n    type Alias[TC] = int\n    if TC:\n        import b\n", True),
        ("type TC = int\ndef consumer():\n    if TC:\n        import b\n", False),
    ],
)
def test_alias_bindings_preserve_typing_guard_classification(
    tmp_path: Path, source: str, type_only: bool
) -> None:
    result = _cycle(tmp_path, "from typing import TYPE_CHECKING as TC\n" + source)
    assert result.complete
    fact, = (fact for fact in result.import_facts if fact.base_module == "b")
    assert fact.type_only is type_only


@pytest.mark.parametrize(
    "source",
    [
        LOADER + 'type load = int\nload("b")\n',
        LOADER + 'type Alias[load] = (lambda: load("b"))\n',
        LOADER + 'type Alias[T: load("b"), load] = T\n',
        LOADER + 'type Alias[T: (int, load("b")), load] = T\n',
        LOADER + 'def outer(load):\n    type Alias = load("b")\n',
        LOADER + 'def outer():\n    type Alias = load("b")\n    load = object()\n',
        LOADER + 'type Alias = [load("b") for load in callbacks]\n',
        'def setup():\n    global load\n    '
        + LOADER
        + 'type Alias[load] = load("b")\n',
        'def outer():\n    load = object()\n'
        '    def setup():\n        nonlocal load\n        '
        + LOADER
        + '    type Alias[load] = (lambda: load("b"))\n',
        'def setup():\n    global load\n    '
        + LOADER
        + 'class Container[load]:\n    type Alias = load("b")\n'
        '    type NestedAlias = (lambda: load("b"))\n',
    ],
)
def test_alias_bindings_do_not_invent_loader_calls(tmp_path: Path, source: str) -> None:
    result = _cycle(tmp_path, source)
    assert result.complete
    assert not _dynamic_facts(result)
    assert not result.diagnostics


@pytest.mark.skipif(sys.version_info < (3, 13), reason="PEP 696 syntax")
def test_all_alias_parameters_shadow_loaders_before_defaults(tmp_path: Path) -> None:
    result = _cycle(
        tmp_path, LOADER + 'type Alias[T = load("b"), load = int] = tuple[T, load]\n'
    )
    assert result.complete
    assert not _dynamic_facts(result)
    assert not result.diagnostics


@pytest.mark.parametrize(
    ("source", "has_dynamic_import"),
    [
        pytest.param(
            LOADER + 'class Container:\n    load = object()\n    type Alias = load("b")\n',
            False,
            id="direct-expression-sees-class-shadow",
        ),
        pytest.param(
            'class Container:\n    '
            + LOADER
            + '    type Alias = load("b")\n',
            True,
            id="direct-expression-sees-class-loader",
        ),
        pytest.param(
            LOADER
            + 'class Container:\n    load = object()\n    type Alias = (lambda: load("b"))\n',
            True,
            id="lambda-skips-class-shadow",
        ),
        pytest.param(
            'class Container:\n    '
            + LOADER
            + '    type Alias = (lambda: load("b"))\n',
            False,
            id="lambda-skips-class-loader",
        ),
        pytest.param(
            LOADER
            + 'class Container:\n    load = object()\n    type Alias = [load("b") for _ in values]\n',
            True,
            id="comprehension-body-skips-class-shadow",
        ),
        pytest.param(
            'class Container:\n    '
            + LOADER
            + '    type Alias = [None for _ in load("b")]\n',
            True,
            id="comprehension-iterable-sees-class-loader",
        ),
        pytest.param(
            'class Outer:\n    '
            + LOADER
            + '    class Inner:\n        type Alias = load("b")\n',
            False,
            id="nested-class-skips-outer-class-loader",
        ),
        pytest.param(
            'class Container:\n    '
            + LOADER
            + '    type Alias[T: load("b")] = T\n',
            True,
            id="bound-sees-class-loader",
        ),
        pytest.param(
            LOADER
            + 'class Container:\n'
            '    type Alias = load("b")\n'
            '    value = Alias.__value__\n'
            '    load = object()\n',
            True,
            id="alias-can-evaluate-before-later-class-shadow",
        ),
        pytest.param(
            LOADER
            + 'class Container:\n'
            '    load = object()\n'
            '    type Alias = load("b")\n'
            '    del load\n'
            'Container.Alias.__value__\n',
            True,
            id="deleted-class-shadow-restores-global-loader",
        ),
        pytest.param(
            LOADER
            + 'class Container:\n'
            '    try:\n'
            '        raise ValueError()\n'
            '    except ValueError as load:\n'
            '        type Alias = load("b")\n'
            'Container.Alias.__value__\n',
            True,
            id="exception-handler-cleanup-restores-global-loader",
        ),
    ],
)
def test_class_alias_expressions_use_their_lexical_namespace(
    tmp_path: Path, source: str, has_dynamic_import: bool
) -> None:
    result = _cycle(tmp_path, source)
    assert result.complete
    assert len(_dynamic_facts(result)) == int(has_dynamic_import)
    assert all(d.code == "dynamic_import_ignored" for d in result.diagnostics)


@pytest.mark.parametrize(
    ("source", "scope", "type_only"),
    [
        (LOADER + 'type Alias = load("b")\n', ImportScope.MODULE, False),
        (
            LOADER + 'def outer():\n    type Alias = load("b")\n',
            ImportScope.LOCAL,
            False,
        ),
        (
            LOADER + 'class Container:\n    type Alias = load("b")\n',
            ImportScope.LOCAL,
            False,
        ),
        (
            LOADER + 'type Alias = (lambda: load("b"))\n',
            ImportScope.LOCAL,
            False,
        ),
        (
            LOADER
            + 'from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    type Alias = load("b")\n',
            ImportScope.MODULE,
            True,
        ),
    ],
)
def test_alias_imports_preserve_containing_scope_and_typing_guard(
    tmp_path: Path, source: str, scope: ImportScope, type_only: bool
) -> None:
    result = _cycle(tmp_path, source)
    fact, = _dynamic_facts(result)
    assert result.complete
    assert fact.scope is scope
    assert fact.type_only is type_only
    assert (("a", "b") in {
        (edge.source, edge.target) for edge in result.architecture_dependencies
    }) is not type_only


def test_alias_fact_locations_and_ids_are_stable(tmp_path: Path) -> None:
    source = LOADER + 'type Alias[T: load("b")] = tuple[T, load("b")]\n'
    first = _cycle(tmp_path, source)
    second = analyse(tmp_path, policy=GraphPolicy(include_type_only=False))
    facts = _dynamic_facts(first)
    assert first.import_facts == second.import_facts
    assert len({fact.id for fact in facts}) == 2
    for fact in facts:
        assert fact.path == "a.py"
        assert fact.line == fact.end_line == 2
        assert fact.source_segment == 'load("b")'
        assert source.splitlines()[1][fact.column:fact.end_column] == 'load("b")'


@pytest.mark.parametrize("dynamic", [False, True])
def test_cli_alias_reports_are_complete_and_keep_existing_check_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], dynamic: bool
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    output = tmp_path / "out"
    source = (
        LOADER + 'type Alias = load("b")\n'
        if dynamic
        else 'def explode():\n    raise RuntimeError("source was executed")\ntype Alias = explode()\n'
    )
    (root / "a.py").write_text(source, encoding="utf-8")
    (root / "b.py").write_text("import a\n", encoding="utf-8")
    assert main([str(root), "--output-dir", str(output), "--check"]) == (
        4 if dynamic else 0
    )
    document = json.loads((output / "dependency-graph.json").read_text())
    assert document["analysis"]["complete"] is True
    assert document["quality"]["score"] is not None
    assert "unsupported_annotation_scope" not in capsys.readouterr().err
    assert "flowchart TD" in (output / "dependency-dag.md").read_text()
