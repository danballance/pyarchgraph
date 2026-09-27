"""Source failures preserve diagnostics and label partial reports incomplete."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from pyarchgraph.adapters import extraction
from pyarchgraph.adapters.discovery import FileSystemSourceDiscovery
from pyarchgraph.composition import ApplicationFactory
from pyarchgraph.domain.model import AnalysisOptions, Severity, SourceModule


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

    result = (
        ApplicationFactory()
        .create_fact_source()
        .collect(tmp_path, FileSystemSourceDiscovery().discover(tmp_path).modules)
    )
    assert [(Path(fact.path).stem, fact.base_module) for fact in result.facts] == [
        ("good", "target")
    ]
    assert [(item.code, item.path, item.severity) for item in result.diagnostics] == [
        ("source_analysis_limit", "broken.py", Severity.ERROR)
    ]
    report = (
        ApplicationFactory()
        .create_analyzer()
        .analyse((tmp_path,), options=AnalysisOptions())
    )
    assert report.status == "incomplete"
    assert "source_analysis_limit" in {
        item.code for item in report.coverage.diagnostics
    }


def test_cli_analysis_limit_emits_incomplete_partial_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _sources(tmp_path)
    _inject_parse_limit(monkeypatch)

    assert ApplicationFactory().create_cli().run([str(tmp_path)]) == 2
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert document["status"] == "incomplete"
    assert "source_analysis_limit" in {
        item["code"] for item in document["coverage"]["diagnostics"]
    }
    assert captured.err == ""


def test_valid_long_expression_does_not_require_recursive_traversal(
    tmp_path: Path,
) -> None:
    source = "value = " + "+".join(["1"] * 800) + "\nimport other\n"
    compile(source, "long.py", "exec")
    (tmp_path / "long.py").write_text(source)
    (tmp_path / "good.py").write_text("import other\n")
    (tmp_path / "other.py").write_text("")

    result = (
        ApplicationFactory()
        .create_fact_source()
        .collect(tmp_path, FileSystemSourceDiscovery().discover(tmp_path).modules)
    )

    assert result.diagnostics == ()
    assert [(Path(fact.path).stem, fact.base_module) for fact in result.facts] == [
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

    result = (
        ApplicationFactory().create_fact_source().collect(tmp_path, (_module("pipe"),))
    )

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

    result = (
        ApplicationFactory()
        .create_fact_source()
        .collect(tmp_path, (_module("source"),))
    )

    assert result.diagnostics == ()
    assert [(Path(fact.path).stem, fact.base_module) for fact in result.facts] == [
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

    result = (
        ApplicationFactory()
        .create_fact_source()
        .collect(tmp_path, FileSystemSourceDiscovery().discover(tmp_path).modules)
    )
    assert [(Path(fact.path).stem, fact.base_module) for fact in result.facts] == [
        ("good", "target")
    ]
    assert [(item.code, item.path, item.severity) for item in result.diagnostics] == [
        ("source_read_error", "broken.py", Severity.ERROR)
    ]
    report = (
        ApplicationFactory()
        .create_analyzer()
        .analyse((tmp_path,), options=AnalysisOptions())
    )
    assert report.status == "incomplete"
    assert "source_read_error" in {item.code for item in report.coverage.diagnostics}

    assert ApplicationFactory().create_cli().run([str(tmp_path)]) == 2
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert document["status"] == "incomplete"
    assert "source_read_error" in {
        item["code"] for item in document["coverage"]["diagnostics"]
    }
    assert any(
        item["path"].endswith("broken.py")
        for item in document["coverage"]["diagnostics"]
    )
    assert captured.err == ""
