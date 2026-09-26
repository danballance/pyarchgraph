from __future__ import annotations

import ast
import hashlib
import json
import random
from pathlib import Path

import pytest

from pyarchgraph import analyse, extraction
from pyarchgraph.discovery import discover_modules
from pyarchgraph.extraction import AstImportFactSource
from pyarchgraph.model import ImportSyntax, Severity, SourceModule


def _module(module_id: str, path: str) -> SourceModule:
    return SourceModule(
        id=module_id,
        path=path,
        is_package=False,
        parent_package=module_id.rpartition(".")[0] or None,
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


def test_unicode_fact_columns_count_characters(tmp_path: Path) -> None:
    source = (
        "é = '\u2028🦉'; import bêta\n"
        "é = 1; from 插件 import (\n    value as renamed)\n"
    )
    (tmp_path / "mod.py").write_text(source, encoding="utf-8")
    result = AstImportFactSource().collect(tmp_path, (_module("mod", "mod.py"),))
    lines = source.split("\n")

    assert result.diagnostics == ()
    assert [(fact.line, fact.column) for fact in result.facts] == [
        (1, lines[0].index("import")),
        (2, lines[1].index("from")),
    ]
    for fact in result.facts:
        assert fact.end_line is not None and fact.end_column is not None
        assert fact.end_column == len(lines[fact.end_line - 1])
        selected = lines[fact.line - 1 : fact.end_line]
        selected[-1] = selected[-1][: fact.end_column]
        selected[0] = selected[0][fact.column :]
        assert "\n".join(selected) == fact.source_segment


def test_unicode_columns_are_one_based_in_public_findings(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        "é = 1; import b\né = 1; __import__('b')\né = 1; __import__(target)\n",
        encoding="utf-8",
    )
    (tmp_path / "b.py").write_text("import a\n", encoding="utf-8")
    (cycle,) = analyse(tmp_path).findings
    assert cycle.kind == "cycle"
    edge = next(edge for edge in cycle.witness if edge.source == "a")
    assert [(item.line, item.column) for item in edge.evidence] == [
        (1, 8),
    ]


def test_unicode_syntax_error_columns_are_not_converted_twice(tmp_path: Path) -> None:
    source = "é = 1; import\n"
    (tmp_path / "mod.py").write_text(source, encoding="utf-8")
    with pytest.raises(SyntaxError) as caught:
        ast.parse(source)
    result = AstImportFactSource().collect(tmp_path, (_module("mod", "mod.py"),))

    (diagnostic,) = result.diagnostics
    assert diagnostic.code == "source_syntax_error"
    assert caught.value.offset is not None
    assert diagnostic.column == caught.value.offset - 1


@pytest.mark.parametrize(
    "body",
    [
        "def function():\n    {statement}\n",
        "async def function():\n    {statement}\n",
        "class Container:\n    {statement}\n",
        "class Container:\n    def method(self):\n        {statement}\n",
        "if False:\n    {statement}\n",
        "if True:\n    pass\nelse:\n    {statement}\n",
        "if TYPE_CHECKING:\n    {statement}\n",
        "if TC and condition:\n    {statement}\n",
        "if typing.TYPE_CHECKING:\n    {statement}\n",
        "for value in []:\n    {statement}\n",
        "while False:\n    {statement}\n",
        "try:\n    pass\nexcept Exception:\n    {statement}\n",
        "try:\n    pass\nfinally:\n    {statement}\n",
        "try:\n    pass\nexcept* Exception:\n    {statement}\n",
        "with context:\n    {statement}\n",
        "match value:\n    case 1:\n        {statement}\n",
    ],
)
@pytest.mark.parametrize(
    "statement", ["import target as dependency", "from . import target as dependency"]
)
def test_explicit_imports_are_collected_in_every_statement_context(
    tmp_path: Path, body: str, statement: str
) -> None:
    source = (
        "import typing\nfrom typing import TYPE_CHECKING, TYPE_CHECKING as TC\n"
        + body.format(statement=statement)
    )
    (tmp_path / "mod.py").write_text(source, encoding="utf-8")

    result = AstImportFactSource().collect(tmp_path, (_module("pkg.mod", "mod.py"),))

    assert result.diagnostics == ()
    assert len(result.facts) == 4
    fact = result.facts[-1]
    assert fact.source_segment == statement
    assert fact.as_name == fact.bound_name == "dependency"
    assert fact.base_module == (None if statement.startswith("from") else "target")
    assert fact.imported_name == ("target" if statement.startswith("from") else None)
    assert fact.relative_level == int(statement.startswith("from"))


@pytest.mark.parametrize(
    "expression",
    [
        '__import__("b")',
        "__import__(name=target)",
        '__import__("b", globals(), locals(), (), 1)',
        'importlib.import_module("b")',
        'loader.import_module("b")',
        'load(name="b")',
        'load(".b", package="pkg")',
        "load(variable)",
        'copied("b")',
        'builtin_alias("b")',
    ],
)
def test_dynamic_import_calls_and_aliases_have_no_evidence_or_findings(
    tmp_path: Path, expression: str
) -> None:
    (tmp_path / "a.py").write_text(
        "import importlib\nimport importlib as loader\n"
        "from importlib import import_module as load\n"
        "copied = load\nbuiltin_alias = __import__\n" + expression + "\n",
        encoding="utf-8",
    )
    (tmp_path / "b.py").write_text("import a\n", encoding="utf-8")

    result = AstImportFactSource().collect(tmp_path, discover_modules(tmp_path).modules)

    assert result.diagnostics == ()
    assert [(fact.source, fact.base_module) for fact in result.facts] == [
        ("a", "importlib"),
        ("a", "importlib"),
        ("a", "importlib"),
        ("b", "a"),
    ]
    report = analyse(tmp_path)
    assert report.dependency_count == 1
    assert report.findings == ()


@pytest.mark.parametrize("postponed", [False, True])
def test_annotations_and_other_expressions_are_not_interpreted(
    tmp_path: Path, postponed: bool
) -> None:
    source = "from __future__ import annotations\n" if postponed else ""
    source += """from importlib import import_module as load
from typing import TYPE_CHECKING
value: load("b")
class Container(load("b")):
    field: __import__("b")
    @load("b")
    def method(self, value: load("b") = load("b")) -> load("b"):
        local: load("b")
        load("b")
        return (lambda: load("b"))()
    computed = [load("b") for _ in values]
if TYPE_CHECKING:
    load("b")
"""
    (tmp_path / "a.py").write_text(source, encoding="utf-8")
    (tmp_path / "b.py").write_text("import a\n", encoding="utf-8")

    result = AstImportFactSource().collect(tmp_path, discover_modules(tmp_path).modules)

    assert result.diagnostics == ()
    assert [fact.base_module for fact in result.facts] == (
        (["__future__"] if postponed else []) + ["importlib", "typing", "a"]
    )
    assert analyse(tmp_path).findings == ()


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


def test_prefix_lengths_match_pairwise_reference_for_collisions_and_duplicates() -> (
    None
):
    rng = random.Random(420)
    digests = [hashlib.sha256(str(index).encode()).hexdigest() for index in range(150)]
    digests.extend(["a" * 12 + "0" * 52, "a" * 12 + "1" * 52, "a" * 63 + "b", "a" * 64])
    digests.extend(digests[:3])
    rng.shuffle(digests)

    expected = []
    for digest in digests:
        length = 12
        while any(
            other != digest and other.startswith(digest[:length]) for other in digests
        ):
            length += 1
        expected.append(length)

    assert extraction._minimum_unique_prefix_lengths(tuple(digests)) == tuple(expected)
    assert extraction._minimum_unique_prefix_lengths(()) == ()
    assert extraction._minimum_unique_prefix_lengths(("f" * 64,)) == (12,)
