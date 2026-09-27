"""Differential checks against Python's source-segment implementation."""

import ast
from dataclasses import replace

import pytest

from pyarchgraph.extraction import (
    _collect_import_facts,
    _SourceText,
    canonicalise_fact_ids,
)
from pyarchgraph.model import SourceModule


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("trailing", [False, True])
def test_cached_segments_and_columns_match_reference(newline, trailing):
    source = newline.join(
        [
            "# ordinary comments",
            "é = '\u2028\u2029\v🦉'; import bêta, γ as g",
            "from pkg import (",
            "    first, # comment",
            "    second as renamed,",
            ")",
            "\fimport last; import also",
            "def f():",
            "\tfrom nested import value",
        ]
    ) + (newline if trailing else "")
    tree = ast.parse(source)
    text = _SourceText(source)
    statements = {
        (node.lineno, text.column(node.lineno, node.col_offset)): node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    }
    facts = tuple(
        _collect_import_facts(
            SourceModule("opaque", "source.py", False, None), source, tree
        )
    )
    reference = []
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for fact in facts:
        node = statements[(fact.line, fact.column)]
        reference.append(
            replace(
                fact,
                source_segment=ast.get_source_segment(source, node),
                column=len(lines[node.lineno - 1].encode()[: node.col_offset].decode()),
                end_column=len(
                    lines[node.end_lineno - 1].encode()[: node.end_col_offset].decode()
                ),
            )
        )
    assert facts == tuple(reference)
    assert canonicalise_fact_ids(facts) == canonicalise_fact_ids(reference)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            assert text.segment(node) == ast.get_source_segment(source, node)


def test_missing_location_returns_none_like_reference():
    text = _SourceText("import target\n")
    node = ast.Import(names=[ast.alias(name="target")])
    assert text.segment(node) is ast.get_source_segment("import target\n", node) is None


def test_one_snippet_extraction_per_statement_not_alias(monkeypatch):
    source = "from pkg import " + ", ".join(f"symbol_{i}" for i in range(100)) + "\n"
    calls = 0
    original = _SourceText.segment

    def segment(self, node):
        nonlocal calls
        calls += 1
        return original(self, node)

    monkeypatch.setattr(_SourceText, "segment", segment)
    facts = tuple(
        _collect_import_facts(
            SourceModule("opaque", "source.py", False, None), source, ast.parse(source)
        )
    )
    assert len(facts) == 100
    assert calls == 1
    assert all(fact.source_segment == source.rstrip("\n") for fact in facts)
