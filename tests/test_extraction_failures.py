"""Source failures preserve low-level diagnostics and prevent partial reports."""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from pyarchgraph import AnalysisError, analyse, extraction
from pyarchgraph.cli import main
from pyarchgraph.discovery import discover_modules
from pyarchgraph.extraction import AstImportFactSource
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

    result = AstImportFactSource().collect(tmp_path, discover_modules(tmp_path).modules)
    assert [(fact.source, fact.base_module) for fact in result.facts] == [
        ("good", "target")
    ]
    assert [(item.code, item.path, item.severity) for item in result.diagnostics] == [
        ("source_analysis_limit", "broken.py", Severity.ERROR)
    ]
    with pytest.raises(AnalysisError, match="source_analysis_limit"):
        analyse(tmp_path)


@pytest.mark.parametrize("stage", ["parse", "traversal"])
def test_cli_analysis_limit_emits_no_partial_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stage: str,
) -> None:
    _sources(tmp_path)
    _inject_limit(monkeypatch, stage)

    assert main([str(tmp_path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "source_analysis_limit" in captured.err
    assert "Traceback" not in captured.err


def test_valid_long_expression_is_analysed_or_explicitly_incomplete(
    tmp_path: Path,
) -> None:
    source = "value = " + "+".join(["1"] * 800) + "\nimport other\n"
    compile(source, "long.py", "exec")
    (tmp_path / "long.py").write_text(source)
    (tmp_path / "good.py").write_text("import other\n")
    (tmp_path / "other.py").write_text("")

    result = AstImportFactSource().collect(tmp_path, discover_modules(tmp_path).modules)

    pairs = [(fact.source, fact.base_module) for fact in result.facts]
    assert ("good", "other") in pairs
    if not result.diagnostics:
        assert ("long", "other") in pairs
        assert result.diagnostics == ()
    else:
        assert pairs == [("good", "other")]
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
