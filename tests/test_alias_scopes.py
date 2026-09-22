"""Control-flow and lexical-scope regressions for source evidence."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pyarchgraph import GraphPolicy, analyse
from pyarchgraph.findings import check_status
from pyarchgraph.model import ImportSyntax


LOADER = "from importlib import import_module as load\n"
GUARD = "from typing import TYPE_CHECKING as TC\n"


def cycle(tmp_path: Path, source: str):
    (tmp_path / "a.py").write_text(source, encoding="utf-8")
    (tmp_path / "b.py").write_text("import a\n", encoding="utf-8")
    return analyse(tmp_path, policy=GraphPolicy(include_type_only=False))


@pytest.mark.parametrize(
    "source",
    [
        LOADER + 'if False:\n    load = object()\nelse:\n    load("b")\n',
        LOADER + 'if flag:\n    load = object()\nload("b")\n',
        "if flag:\n    " + LOADER + "else:\n    " + LOADER + 'load("b")\n',
        LOADER
        + 'if flag:\n    if other:\n        load = object()\n    else:\n        load("b")\n',
        LOADER + 'try:\n    load = object()\nexcept Exception:\n    load("b")\n',
        LOADER + 'try:\n    pass\nexcept Exception:\n    load = object()\nload("b")\n',
        LOADER + 'for item in []:\n    load = object()\nload("b")\n',
        LOADER + 'while flag:\n    load = object()\nload("b")\n',
        LOADER
        + 'match value:\n    case 1:\n        load = object()\n    case 2:\n        load("b")\n',
        'def f():\n    load("b")\n' + LOADER + "f()\n",
        LOADER + 'def f():\n    load("b")\nf()\nload = object()\nf()\n',
        LOADER
        + 'class Outer:\n    load = object()\n    class Inner:\n        value = load("b")\n',
        "def f():\n    "
        + LOADER
        + '    class Outer:\n        load = object()\n        class Inner:\n            value = load("b")\n',
        'def f():\n    def inner():\n        load("b")\n    '
        + LOADER
        + "    inner()\n",
        LOADER + 'def f():\n    global load\n    load("b")\n',
        "def f():\n    "
        + LOADER
        + '    def inner():\n        nonlocal load\n        load("b")\n',
    ],
)
def test_possible_loader_paths_retain_candidates(tmp_path: Path, source: str):
    result = cycle(tmp_path, source)
    assert result.complete
    assert not result.dependency_resolution_complete
    assert check_status(result) == "needs_review"
    facts = [f for f in result.import_facts if f.base_module == "b"]
    assert len(facts) == 1
    assert facts[0].syntax is ImportSyntax.DYNAMIC_IMPORT
    assert {(e.source, e.target) for e in result.architecture_dependencies} == {
        ("a", "b"),
        ("b", "a"),
    }
    assert result.findings[0]["certainty"] == "possible"
    assert any(
        d.code == "dynamic_import_ignored" and d.line == facts[0].line
        for d in result.diagnostics
    )


@pytest.mark.parametrize(
    "source",
    [
        "if True:\n    TC = True\nelse:\n    " + GUARD + "if TC:\n    import b\n",
        GUARD + "for _ in []:\n    TC = True\nif TC:\n    import b\n",
        GUARD
        + "try:\n    TC = True\nexcept Exception:\n    pass\nif TC:\n    import b\n",
        GUARD + "match value:\n    case TC:\n        if TC:\n            import b\n",
        GUARD
        + "def f(value):\n    match value:\n        case TC:\n            if TC:\n                import b\nf(True)\n",
        GUARD
        + "def f(value):\n    match value:\n        case [*TC]:\n            if TC:\n                import b\n",
        GUARD
        + 'def f(value):\n    match value:\n        case {"key": _, **TC}:\n            if TC:\n                import b\n',
        GUARD + "[(TC := True) for _ in range(1)]\nif TC:\n    import b\n",
        GUARD + "{(TC := True) for _ in range(1)}\nif TC:\n    import b\n",
        GUARD + "{_: (TC := True) for _ in range(1)}\nif TC:\n    import b\n",
        GUARD + "g = ((TC := True) for _ in range(1))\nif TC:\n    import b\n",
        GUARD
        + "[[(TC := True) for _ in range(1)] for _ in range(1)]\nif TC:\n    import b\n",
        GUARD
        + "def f():\n    [(TC := True) for _ in range(1)]\n    if TC:\n        import b\n",
        GUARD + "def f():\n    if TC:\n        import b\nTC = True\nf()\n",
        "def outer():\n    "
        + GUARD
        + "    def inner():\n        if TC:\n            import b\n    TC = True\n    inner()\n",
    ],
)
def test_runtime_guards_keep_exact_dependencies(tmp_path: Path, source: str):
    result = cycle(tmp_path, source)
    assert result.complete
    assert result.dependency_resolution_complete
    assert check_status(result) == "fail"
    assert not next(f for f in result.import_facts if f.base_module == "b").type_only
    assert {(e.source, e.target) for e in result.architecture_dependencies} == {
        ("a", "b"),
        ("b", "a"),
    }
    assert result.findings[0]["certainty"] == "definite"


@pytest.mark.parametrize(
    "source",
    [
        LOADER + 'match callback:\n    case load:\n        load("b")\n',
        LOADER
        + 'def f(value):\n    load("b")\n    match value:\n        case load:\n            pass\n',
        LOADER + 'def f(load):\n    def inner():\n        load("b")\n',
        LOADER + 'def f():\n    load = object()\n    def inner():\n        load("b")\n',
    ],
)
def test_lexical_bindings_do_not_invent_loaders(tmp_path: Path, source: str):
    result = cycle(tmp_path, source)
    assert not any(f.syntax is ImportSyntax.DYNAMIC_IMPORT for f in result.import_facts)
    assert not result.diagnostics


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 syntax")
@pytest.mark.parametrize("definition", ["def f[TC]():", "class f[TC]:"])
def test_generic_parameter_shadows_guard(tmp_path: Path, definition: str):
    result = cycle(tmp_path, GUARD + definition + "\n    if TC:\n        import b\n")
    assert result.complete
    assert check_status(result) == "fail"
    assert not next(f for f in result.import_facts if f.base_module == "b").type_only


@pytest.mark.parametrize(
    "source",
    [
        GUARD + "[TC for TC in values]\nif TC:\n    import b\n",
        GUARD + "def f():\n    if TC:\n        import b\n",
        "def outer():\n    "
        + GUARD
        + "    def inner():\n        if TC:\n            import b\n",
    ],
)
def test_stable_guards_and_comprehension_targets(tmp_path: Path, source: str):
    result = cycle(tmp_path, source)
    assert check_status(result) == "pass"
    assert next(f for f in result.import_facts if f.base_module == "b").type_only


@pytest.mark.parametrize(
    "source",
    [
        GUARD + "for _ in range(2):\n    if TC:\n        import b\n    TC = True\n",
        GUARD
        + "try:\n    if flag:\n        TC = True\n        raise ValueError\n        "
        + GUARD
        + "except Exception:\n    if TC:\n        import b\n",
        GUARD
        + "match value:\n    case 1 if (TC := True):\n        pass\n    case _:\n        if TC:\n            import b\n",
        GUARD
        + "def f():\n    if TC:\n        import b\n    def inner(value=(TC := True)):\n        pass\n",
        GUARD + "match value:\n    case [*TC]:\n        if TC:\n            import b\n",
        GUARD
        + 'match value:\n    case {"key": _, **TC}:\n        if TC:\n            import b\n',
    ],
)
def test_nested_paths_and_binding_expressions_keep_runtime_imports(
    tmp_path: Path, source: str
):
    result = cycle(tmp_path, source)
    assert result.complete
    assert check_status(result) == "fail"
    assert not next(f for f in result.import_facts if f.base_module == "b").type_only


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 syntax")
@pytest.mark.parametrize(
    "source",
    [
        'def f[T: load("b")]():\n    pass\n',
        'class C[T: load("b")]:\n    pass\n',
    ],
)
def test_unsupported_lazy_annotation_scopes_are_explicit(tmp_path: Path, source: str):
    result = cycle(tmp_path, LOADER + source)
    assert not result.complete
    assert not result.dependency_resolution_complete
    assert result.quality.score is None
    assert any(d.code == "unsupported_annotation_scope" for d in result.diagnostics)


@pytest.mark.skipif(sys.version_info < (3, 13), reason="PEP 696 syntax")
def test_unsupported_type_parameter_default_is_explicit(tmp_path: Path):
    result = cycle(tmp_path, LOADER + 'def f[T = load("b")]():\n    pass\n')
    assert not result.complete
    assert any(d.code == "unsupported_annotation_scope" for d in result.diagnostics)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 syntax")
def test_generic_defaults_use_outer_scope_and_bases_use_parameter_scope(tmp_path: Path):
    result = cycle(
        tmp_path,
        LOADER
        + 'def f[load](value=load("b")):\n    pass\nclass C[load](load("b")):\n    pass\n',
    )
    facts = [f for f in result.import_facts if f.base_module == "b"]
    assert [f.line for f in facts] == [2]
    assert result.complete
    assert check_status(result) == "needs_review"


def test_deferred_builtin_alias_can_precede_module_shadow(tmp_path: Path):
    result = cycle(
        tmp_path, 'def f():\n    __import__("b")\nf()\n__import__ = object()\n'
    )
    assert result.complete
    assert check_status(result) == "needs_review"
    assert any(f.base_module == "b" for f in result.import_facts)


@pytest.mark.parametrize(
    "source",
    [
        LOADER + 'load += load("b")\n',
        LOADER + 'def f(value: (lambda: load("b"))()):\n    pass\n',
        LOADER + 'def f(value: [load("b") for _ in range(1)]):\n    pass\n',
    ],
)
def test_expression_namespaces_and_augmented_assignment(tmp_path: Path, source: str):
    result = cycle(tmp_path, source)
    assert result.complete
    assert check_status(result) == "needs_review"
    assert len([f for f in result.import_facts if f.base_module == "b"]) == 1


@pytest.mark.parametrize(
    "source",
    [
        'try:\n    raise ExceptionGroup("both", [ValueError(), TypeError()])\nexcept* ValueError:\n    '
        + LOADER
        + 'except* TypeError:\n    load("b")\n',
        LOADER
        + 'def f():\n    load = object()\n    class C:\n        global load\n        value = load("b")\n',
    ],
)
def test_exception_groups_and_explicit_class_globals(tmp_path: Path, source: str):
    result = cycle(tmp_path, source)
    assert result.complete
    assert check_status(result) == "needs_review"
    assert any(f.base_module == "b" for f in result.import_facts)
