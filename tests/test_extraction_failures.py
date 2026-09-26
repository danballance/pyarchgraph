"""Source failures preserve low-level diagnostics and prevent partial reports."""

from __future__ import annotations

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
    (root / "broken.py").write_text("import target\nimport another\n")
    (root / "good.py").write_text("import target\n")
    (root / "target.py").write_text("")


def _inject_parse_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    original_parse = extraction.ast.parse

    def parse(source, filename, *args, **kwargs):
        if filename == "broken.py":
            raise RecursionError("injected parsing limit")
        return original_parse(source, filename, *args, **kwargs)

    monkeypatch.setattr(extraction.ast, "parse", parse)


def test_source_parser_limit_reports_failure_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _sources(tmp_path)
    _inject_parse_limit(monkeypatch)

    result = AstImportFactSource().collect(tmp_path, discover_modules(tmp_path).modules)
    assert [(fact.source, fact.base_module) for fact in result.facts] == [
        ("good", "target")
    ]
    assert [(item.code, item.path, item.severity) for item in result.diagnostics] == [
        ("source_analysis_limit", "broken.py", Severity.ERROR)
    ]
    with pytest.raises(AnalysisError, match="source_analysis_limit"):
        analyse(tmp_path)


def test_cli_analysis_limit_emits_no_partial_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _sources(tmp_path)
    _inject_parse_limit(monkeypatch)

    assert main([str(tmp_path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "source_analysis_limit" in captured.err
    assert "Traceback" not in captured.err


def test_valid_long_expression_does_not_require_recursive_traversal(
    tmp_path: Path,
) -> None:
    source = "value = " + "+".join(["1"] * 800) + "\nimport other\n"
    compile(source, "long.py", "exec")
    (tmp_path / "long.py").write_text(source)
    (tmp_path / "good.py").write_text("import other\n")
    (tmp_path / "other.py").write_text("")

    result = AstImportFactSource().collect(tmp_path, discover_modules(tmp_path).modules)

    assert result.diagnostics == ()
    assert [(fact.source, fact.base_module) for fact in result.facts] == [
        ("good", "other"),
        ("long", "other"),
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


def test_unreadable_source_fails_collection_analysis_and_cli(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _sources(tmp_path)
    original_open = extraction.tokenize.open

    def open_source(path):
        if Path(path).name == "broken.py":
            raise PermissionError("source is unreadable")
        return original_open(path)

    monkeypatch.setattr(extraction.tokenize, "open", open_source)

    result = AstImportFactSource().collect(tmp_path, discover_modules(tmp_path).modules)
    assert [(fact.source, fact.base_module) for fact in result.facts] == [
        ("good", "target")
    ]
    assert [(item.code, item.path, item.severity) for item in result.diagnostics] == [
        ("source_read_error", "broken.py", Severity.ERROR)
    ]
    with pytest.raises(AnalysisError, match="source_read_error"):
        analyse(tmp_path)

    assert main([str(tmp_path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "source_read_error" in captured.err
    assert "broken.py" in captured.err
    assert "Traceback" not in captured.err
