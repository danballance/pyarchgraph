"""Source failures must produce complete reports about incomplete extraction."""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest

from pyarchgraph import analyse, extraction
from pyarchgraph.cli import main
from pyarchgraph.extraction import AstImportFactSource
from pyarchgraph.findings import check_status
from pyarchgraph.model import Severity, SourceModule


def _module(name: str) -> SourceModule:
    return SourceModule(name, f"{name}.py", False, None)


def _sources(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "broken.py").write_text("import target\n__import__('target')\n")
    (root / "good.py").write_text("import target\n")
    (root / "target.py").write_text("")


def _inject_limit(monkeypatch: pytest.MonkeyPatch, stage: str) -> None:
    if stage == "parse":
        original_parse = extraction.ast.parse

        def parse(source, filename, *args, **kwargs):
            if filename == "broken.py":
                raise RecursionError("injected parsing limit")
            return original_parse(source, filename, *args, **kwargs)

        monkeypatch.setattr(extraction.ast, "parse", parse)
    else:
        original_visit = extraction._ImportVisitor.visit

        def visit(visitor, node):
            result = original_visit(visitor, node)
            if visitor._module.id == "broken" and isinstance(node, ast.Module):
                # Simulate exhaustion after the visitor has already collected
                # facts and a dynamic warning from the failing file.
                assert visitor.facts
                assert visitor.diagnostics
                raise RecursionError("injected traversal limit")
            return result

        monkeypatch.setattr(extraction._ImportVisitor, "visit", visit)


@pytest.mark.parametrize("stage", ["parse", "traversal"])
def test_source_analysis_limit_discards_partial_evidence_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    _sources(tmp_path)
    _inject_limit(monkeypatch, stage)

    result = analyse(tmp_path)

    assert result.complete is False
    assert result.dependency_resolution_complete is False
    assert result.quality.score is None
    assert result.quality.unavailable_reason == "incomplete_analysis"
    assert check_status(result) == "needs_review"
    assert [(fact.source, fact.base_module) for fact in result.import_facts] == [
        ("good", "target")
    ]
    assert [
        (edge.source, edge.target) for edge in result.architecture_dependencies
    ] == [("good", "target")]
    assert [(item.code, item.path, item.severity) for item in result.diagnostics] == [
        ("source_analysis_limit", "broken.py", Severity.ERROR)
    ]


@pytest.mark.parametrize("stage", ["parse", "traversal"])
def test_cli_analysis_limit_replaces_old_outputs_with_valid_incomplete_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stage: str,
) -> None:
    source_root = tmp_path / "source"
    output_dir = tmp_path / "out"
    _sources(source_root)
    output_dir.mkdir()
    json_path = output_dir / "dependency-graph.json"
    markdown_path = output_dir / "dependency-dag.md"
    json_path.write_text("previous JSON")
    markdown_path.write_text("previous Markdown")
    _inject_limit(monkeypatch, stage)

    assert main([str(source_root), "--output-dir", str(output_dir), "--check"]) == 1

    document = json.loads(json_path.read_text())
    assert document["analysis"]["complete"] is False
    assert document["analysis"]["dependency_resolution_complete"] is False
    assert document["quality"]["score"] is None
    assert document["diagnostics"][0]["code"] == "source_analysis_limit"
    assert {fact["source"] for fact in document["import_facts"]} == {"good"}
    assert "flowchart TD" in markdown_path.read_text()
    stderr = capsys.readouterr().err
    assert "error[source_analysis_limit]" in stderr
    assert "Traceback" not in stderr
    assert not tuple(output_dir.glob(".*.tmp"))


def test_valid_long_expression_is_analysed_or_explicitly_incomplete(
    tmp_path: Path,
) -> None:
    source = "value = " + "+".join(["1"] * 800) + "\nimport other\n"
    compile(source, "long.py", "exec")
    (tmp_path / "long.py").write_text(source)
    (tmp_path / "good.py").write_text("import other\n")
    (tmp_path / "other.py").write_text("")

    result = analyse(tmp_path)

    pairs = [(fact.source, fact.base_module) for fact in result.import_facts]
    assert ("good", "other") in pairs
    if result.complete:
        assert ("long", "other") in pairs
        assert result.diagnostics == ()
    else:
        assert pairs == [("good", "other")]
        assert result.quality.score is None
        assert [(item.code, item.path) for item in result.diagnostics] == [
            ("source_analysis_limit", "long.py")
        ]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX named pipes")
def test_direct_collector_rejects_fifo_before_opening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os.mkfifo(tmp_path / "pipe.py")

    def forbidden_open(*args, **kwargs):
        pytest.fail("collector tried opening a FIFO, which could block indefinitely")

    monkeypatch.setattr(extraction.tokenize, "open", forbidden_open)

    result = AstImportFactSource().collect(tmp_path, (_module("pipe"),))

    assert result.facts == ()
    assert [(item.code, item.path, item.severity) for item in result.diagnostics] == [
        ("source_not_regular", "pipe.py", Severity.ERROR)
    ]


@pytest.mark.parametrize("symlink", [False, True])
def test_direct_collector_accepts_regular_files_and_file_symlinks(
    tmp_path: Path, symlink: bool
) -> None:
    source_path = tmp_path / "source.py"
    if symlink:
        target = tmp_path / "target.txt"
        target.write_text("import target\n")
        try:
            source_path.symlink_to(target)
        except (OSError, NotImplementedError):
            pytest.skip("file symlinks are unavailable")
    else:
        source_path.write_text("import target\n")

    result = AstImportFactSource().collect(tmp_path, (_module("source"),))

    assert result.diagnostics == ()
    assert [(fact.source, fact.base_module) for fact in result.facts] == [
        ("source", "target")
    ]
