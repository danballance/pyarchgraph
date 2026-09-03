from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from pyarchgraph import extraction
from pyarchgraph.extraction import AstImportFactSource
from pyarchgraph.model import ImportScope, ImportSyntax, Severity, SourceModule


def _module(
    module_id: str,
    path: str,
    *,
    is_package: bool = False,
) -> SourceModule:
    return SourceModule(
        id=module_id,
        path=path,
        is_package=is_package,
        parent_package=(
            module_id if is_package else module_id.rpartition(".")[0] or None
        ),
    )


def test_extracts_one_source_backed_fact_per_alias(tmp_path: Path) -> None:
    source = """import alpha.beta, gamma as g
from base import value as renamed, other
from .service import Thing
from .. import *
"""
    path = tmp_path / "pkg" / "mod.py"
    path.parent.mkdir()
    path.write_text(source, encoding="utf-8")

    result = AstImportFactSource().collect(
        tmp_path, (_module("pkg.mod", "pkg/mod.py"),)
    )

    assert result.diagnostics == ()
    assert len(result.facts) == 6
    assert [fact.alias_index for fact in result.facts] == [0, 1, 0, 1, 0, 0]
    assert [fact.syntax for fact in result.facts] == [
        ImportSyntax.IMPORT,
        ImportSyntax.IMPORT,
        ImportSyntax.IMPORT_FROM,
        ImportSyntax.IMPORT_FROM,
        ImportSyntax.IMPORT_FROM,
        ImportSyntax.IMPORT_FROM,
    ]
    assert [
        (
            fact.base_module,
            fact.imported_name,
            fact.as_name,
            fact.bound_name,
            fact.relative_level,
        )
        for fact in result.facts
    ] == [
        ("alpha.beta", None, None, "alpha", 0),
        ("gamma", None, "g", "g", 0),
        ("base", "value", "renamed", "renamed", 0),
        ("base", "other", None, "other", 0),
        ("service", "Thing", None, "Thing", 1),
        (None, "*", None, "*", 2),
    ]
    assert result.facts[0].source_segment == "import alpha.beta, gamma as g"
    assert result.facts[0].line == 1
    assert result.facts[0].column == 0
    assert result.facts[0].end_line == 1
    assert result.facts[0].end_column == len("import alpha.beta, gamma as g")
    assert all(fact.scope is ImportScope.MODULE for fact in result.facts)


def test_scope_and_exact_type_checking_guard_semantics(tmp_path: Path) -> None:
    source = """if LATE:
    import before_binding
from typing import TYPE_CHECKING as TC
import typing as t
if TC:
    import direct
    if anything:
        import nested
else:
    import runtime_else
if t.TYPE_CHECKING:
    from . import typed_leaf
if TC and anything:
    import unsupported_boolean
def function():
    if TC:
        import local_typed
    import local_runtime
from typing import TYPE_CHECKING as LATE
"""
    path = tmp_path / "pkg" / "mod.py"
    path.parent.mkdir()
    path.write_text(source, encoding="utf-8")

    result = AstImportFactSource().collect(
        tmp_path, (_module("pkg.mod", "pkg/mod.py"),)
    )
    facts = {
        fact.base_module or fact.imported_name or "": fact for fact in result.facts
    }

    assert facts["before_binding"].type_only is False
    assert facts["direct"].type_only is True
    assert facts["nested"].type_only is True
    assert facts["runtime_else"].type_only is False
    assert facts["typed_leaf"].type_only is True
    assert facts["unsupported_boolean"].type_only is False
    assert facts["local_typed"].type_only is True
    assert facts["local_typed"].scope is ImportScope.LOCAL
    assert facts["local_runtime"].type_only is False
    assert facts["local_runtime"].scope is ImportScope.LOCAL


def test_only_direct_dynamic_import_callee_forms_are_diagnosed(
    tmp_path: Path,
) -> None:
    source = """import importlib
__import__("literal")
__import__(computed)
importlib.import_module("direct")
load = importlib.import_module
load("alias-is-ignored")
other.import_module("also-ignored")
"""
    path = tmp_path / "mod.py"
    path.write_text(source, encoding="utf-8")

    result = AstImportFactSource().collect(tmp_path, (_module("mod", "mod.py"),))

    assert [(item.code, item.line, item.column) for item in result.diagnostics] == [
        ("dynamic_import_ignored", 2, 0),
        ("dynamic_import_ignored", 3, 0),
        ("dynamic_import_ignored", 4, 0),
    ]
    assert all(item.severity is Severity.WARNING for item in result.diagnostics)
    assert len(result.facts) == 1
    assert result.facts[0].base_module == "importlib"


def test_read_decode_and_parse_failures_are_stable_and_do_not_stop_collection(
    tmp_path: Path,
) -> None:
    (tmp_path / "good.py").write_text(
        "raise RuntimeError('must not execute')\nimport target\n",
        encoding="utf-8",
    )
    (tmp_path / "bad_decode.py").write_bytes(b"# coding: utf-8\n\xff\n")
    (tmp_path / "bad_syntax.py").write_text("from broken import\n", encoding="utf-8")

    modules = (
        _module("missing", "missing.py"),
        _module("bad_syntax", "bad_syntax.py"),
        _module("good", "good.py"),
        _module("bad_decode", "bad_decode.py"),
    )
    result = AstImportFactSource().collect(tmp_path, modules)

    assert [(item.code, item.path) for item in result.diagnostics] == [
        ("source_decode_error", "bad_decode.py"),
        ("source_read_error", "missing.py"),
        ("source_syntax_error", "bad_syntax.py"),
    ]
    assert all(item.severity is Severity.ERROR for item in result.diagnostics)
    assert all(str(tmp_path) not in item.message for item in result.diagnostics)
    assert [(fact.source, fact.base_module) for fact in result.facts] == [
        ("good", "target")
    ]


def test_fact_ids_and_order_are_deterministic(tmp_path: Path) -> None:
    (tmp_path / "b.py").write_text("import z\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("import y\n", encoding="utf-8")
    a = _module("a", "a.py")
    b = _module("b", "b.py")
    collector = AstImportFactSource()

    forward = collector.collect(tmp_path, (a, b))
    reverse = collector.collect(tmp_path, (b, a))

    assert forward == reverse
    assert [fact.source for fact in forward.facts] == ["a", "b"]
    assert all(
        fact.id.startswith("fact-") and len(fact.id) == len("fact-") + 12
        for fact in forward.facts
    )

    first = forward.facts[0]
    identity = (
        first.source,
        first.path,
        first.line,
        first.column,
        first.end_line,
        first.end_column,
        first.alias_index,
        first.syntax.value,
        first.base_module,
        first.imported_name,
        first.as_name,
        first.bound_name,
        first.relative_level,
        first.scope.value,
        first.type_only,
        first.source_segment,
    )
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    assert first.id == f"fact-{hashlib.sha256(encoded).hexdigest()[:12]}"


def test_fact_id_prefix_collisions_are_extended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "mod.py").write_text("import a, b, c\n", encoding="utf-8")
    digests = {
        "a": "123456789abc0" + "0" * 51,
        "b": "123456789abc1" + "1" * 51,
        "c": "fedcba9876542" + "2" * 51,
    }
    monkeypatch.setattr(
        extraction,
        "_fact_digest",
        lambda fact: digests[fact.base_module or ""],
    )

    result = AstImportFactSource().collect(tmp_path, (_module("mod", "mod.py"),))

    assert [fact.id for fact in result.facts] == [
        "fact-123456789abc0",
        "fact-123456789abc1",
        "fact-fedcba987654",
    ]


def test_full_fact_digest_collision_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "mod.py").write_text("import a, b\n", encoding="utf-8")
    monkeypatch.setattr(extraction, "_fact_digest", lambda fact: "0" * 64)

    with pytest.raises(ValueError, match="unique full SHA-256"):
        AstImportFactSource().collect(tmp_path, (_module("mod", "mod.py"),))
