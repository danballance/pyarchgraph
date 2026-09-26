"""Regressions from independent review of deferred and annotation scopes."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from pyarchgraph.discovery import discover_modules
from pyarchgraph.extraction import AstImportFactSource
from pyarchgraph.model import ImportSyntax


def _cycle(tmp_path: Path, source: str):
    (tmp_path / "a.py").write_text(textwrap.dedent(source), encoding="utf-8")
    (tmp_path / "b.py").write_text("import a\n", encoding="utf-8")
    return AstImportFactSource().collect(tmp_path, discover_modules(tmp_path).modules)


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            """\
            from typing import TYPE_CHECKING as TC
            def mutate():
                global TC
                TC = True
            def consumer():
                if TC:
                    import b
            mutate()
            consumer()
            """,
            id="global-rebinding-in-sibling-function",
        ),
        pytest.param(
            """\
            def outer():
                from typing import TYPE_CHECKING as TC
                def mutate():
                    nonlocal TC
                    TC = True
                def consumer():
                    if TC:
                        import b
                mutate()
                consumer()
            outer()
            """,
            id="nonlocal-rebinding-in-sibling-function",
        ),
        pytest.param(
            """\
            generator = ((TC := True) for _ in range(1))
            from typing import TYPE_CHECKING as TC
            list(generator)
            if TC:
                import b
            """,
            id="generator-write-after-reimport",
        ),
    ],
)
def test_deferred_writes_do_not_prove_typing_only(tmp_path: Path, source: str) -> None:
    result = _cycle(tmp_path, source)
    fact = next(f for f in result.facts if f.base_module == "b")
    assert not fact.type_only
    assert fact.syntax is ImportSyntax.IMPORT


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            """\
            def setup():
                global load
                from importlib import import_module as load
            def consumer():
                load("b")
            setup()
            consumer()
            """,
            id="global-loader-in-sibling-function",
        ),
        pytest.param(
            """\
            from importlib import import_module as load
            def consumer(load: load("b")):
                pass
            consumer.__annotations__
            """,
            id="annotation-skips-parameter-binding",
        ),
        pytest.param(
            """\
            from importlib import import_module as load
            def consumer(value: load("b")):
                load = object()
            consumer.__annotations__
            """,
            id="annotation-skips-body-local-binding",
        ),
        pytest.param(
            """\
            class Container:
                from importlib import import_module as load
                def method(value: load("b")):
                    pass
            Container.method.__annotations__
            """,
            id="method-annotation-sees-class-binding",
        ),
        pytest.param(
            """\
            load = object()
            [load("b") if index else (load := __import__) for index in range(2)]
            """,
            id="comprehension-backedge",
        ),
        pytest.param(
            """\
            count = 0
            load = lambda name: False
            while load("b") if count else True:
                from importlib import import_module as load
                count += 1
                if count > 1:
                    break
            """,
            id="while-condition-backedge",
        ),
        pytest.param(
            """\
            generator = ((load := __import__) for _ in range(1))
            load = object()
            list(generator)
            load("b")
            """,
            id="generator-write-after-rebinding",
        ),
    ],
)
def test_deferred_and_annotation_loaders_retain_candidates(
    tmp_path: Path, source: str
) -> None:
    result = _cycle(tmp_path, source)
    facts = [f for f in result.facts if f.base_module == "b"]
    assert len(facts) == 1
    assert facts[0].syntax is ImportSyntax.DYNAMIC_IMPORT
    assert not facts[0].type_only
    assert any(d.code == "dynamic_import_ignored" for d in result.diagnostics)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 syntax")
def test_generic_class_parameter_is_visible_to_method(tmp_path: Path) -> None:
    result = _cycle(
        tmp_path,
        """\
        from typing import TYPE_CHECKING as T
        class Container[T]:
            def method(self):
                if T:
                    import b
        Container().method()
        """,
    )
    assert not next(f for f in result.facts if f.base_module == "b").type_only


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 syntax")
def test_generic_class_parameter_shadows_loader_in_method(tmp_path: Path) -> None:
    result = _cycle(
        tmp_path,
        """\
        from importlib import import_module as load
        class Container[load]:
            def method(self):
                load("b")
        """,
    )
    assert not any(f.syntax is ImportSyntax.DYNAMIC_IMPORT for f in result.facts)
    assert not result.diagnostics


def test_comprehension_lambda_captures_iteration_variable(tmp_path: Path) -> None:
    result = _cycle(
        tmp_path,
        """\
        from importlib import import_module as load
        callbacks = [lambda: load("b") for load in [lambda _: None]]
        callbacks[0]()
        """,
    )
    assert not any(f.syntax is ImportSyntax.DYNAMIC_IMPORT for f in result.facts)
    assert not result.diagnostics


def test_method_annotation_sees_class_override(tmp_path: Path) -> None:
    result = _cycle(
        tmp_path,
        """\
        from importlib import import_module as load
        class Container:
            load = lambda name: object
            def method(value: load("b")):
                pass
        Container.method.__annotations__
        """,
    )
    assert not any(f.syntax is ImportSyntax.DYNAMIC_IMPORT for f in result.facts)
    assert not result.diagnostics
