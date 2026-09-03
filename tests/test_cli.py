from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyarchgraph import analyse
from pyarchgraph.cli import main
from pyarchgraph.model import (
    Diagnostic,
    FactCollection,
    ImportFact,
    ImportScope,
    ImportSyntax,
    Severity,
    SourceModule,
)


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_cli_writes_complete_outputs_without_executing_source(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source_root = tmp_path / "source"
    output_dir = tmp_path / "out"
    _write(source_root, "pkg/__init__.py", "from . import a\n")
    _write(source_root, "pkg/a.py", "import pkg.b\n")
    _write(source_root, "pkg/b.py", "import pkg.a\n")
    _write(
        source_root,
        "pkg/never_execute.py",
        "raise RuntimeError('the analyser executed target code')\n",
    )

    status = main([str(source_root), "--output-dir", str(output_dir)])

    assert status == 0
    document = json.loads((output_dir / "dependency-graph.json").read_text())
    assert document["analysis"]["complete"] is True
    assert {module["id"] for module in document["modules"]} == {
        "pkg",
        "pkg.a",
        "pkg.b",
        "pkg.never_execute",
    }
    assert sum(node["cyclic"] for node in document["dag"]["nodes"]) == 1
    assert "flowchart LR" in (output_dir / "dependency-dag.md").read_text()
    assert "4 modules" in capsys.readouterr().err


def test_cli_writes_partial_result_and_returns_one(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    output_dir = tmp_path / "out"
    _write(source_root, "good.py", "import os\n")
    _write(source_root, "broken.py", "def nope(:\n")

    status = main([str(source_root), "--output-dir", str(output_dir)])

    assert status == 1
    document = json.loads((output_dir / "dependency-graph.json").read_text())
    assert document["analysis"]["complete"] is False
    assert {item["code"] for item in document["diagnostics"]} == {"source_syntax_error"}
    assert (output_dir / "dependency-dag.md").is_file()


def test_cli_invalid_input_exits_two_without_outputs(tmp_path: Path) -> None:
    output_dir = tmp_path / "out"

    with pytest.raises(SystemExit) as raised:
        main([str(tmp_path / "missing"), "--output-dir", str(output_dir)])

    assert raised.value.code == 2
    assert not output_dir.exists()


def test_cli_rejects_non_file_target_before_replacing_sibling(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    output_dir = tmp_path / "out"
    _write(source_root, "module.py", "VALUE = 1\n")
    output_dir.mkdir()
    json_path = output_dir / "dependency-graph.json"
    json_path.write_text("existing\n", encoding="utf-8")
    (output_dir / "dependency-dag.md").mkdir()

    with pytest.raises(SystemExit) as raised:
        main([str(source_root), "--output-dir", str(output_dir)])

    assert raised.value.code == 2
    assert json_path.read_text(encoding="utf-8") == "existing\n"


def test_cli_outputs_are_byte_identical_across_runs(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    output_dir = tmp_path / "out"
    _write(source_root, "a.py", "import b\nimport b\n")
    _write(source_root, "b.py", "VALUE = 1\n")

    assert main([str(source_root), "--output-dir", str(output_dir)]) == 0
    first_json = (output_dir / "dependency-graph.json").read_bytes()
    first_markdown = (output_dir / "dependency-dag.md").read_bytes()
    assert main([str(source_root), "--output-dir", str(output_dir)]) == 0

    assert (output_dir / "dependency-graph.json").read_bytes() == first_json
    assert (output_dir / "dependency-dag.md").read_bytes() == first_markdown


def test_analyse_accepts_repository_level_fact_source(tmp_path: Path) -> None:
    _write(tmp_path, "only.py", "VALUE = 1\n")

    class EmptyFactSource:
        def __init__(self) -> None:
            self.modules: tuple[SourceModule, ...] | None = None

        def collect(
            self, source_root: Path, modules: tuple[SourceModule, ...]
        ) -> FactCollection:
            assert source_root == tmp_path
            self.modules = modules
            return FactCollection(facts=())

    source = EmptyFactSource()
    result = analyse(tmp_path, fact_source=source)

    assert source.modules == result.modules
    assert result.complete is True
    assert result.import_facts == ()


def test_analyse_canonicalises_injected_facts_and_respects_source_errors(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "a.py", "VALUE = 1\n")
    _write(tmp_path, "b.py", "VALUE = 2\n")

    class InjectedFactSource:
        def collect(
            self, source_root: Path, modules: tuple[SourceModule, ...]
        ) -> FactCollection:
            del source_root, modules
            return FactCollection(
                facts=(
                    ImportFact(
                        id="caller-supplied-id-is-ignored",
                        source="a",
                        path="a.py",
                        line=1,
                        column=0,
                        end_line=1,
                        end_column=8,
                        alias_index=0,
                        syntax=ImportSyntax.IMPORT,
                        source_segment="import b",
                        base_module="b",
                        imported_name=None,
                        as_name=None,
                        bound_name="b",
                        relative_level=0,
                        scope=ImportScope.MODULE,
                        type_only=False,
                    ),
                ),
                diagnostics=(
                    Diagnostic(
                        severity=Severity.ERROR,
                        code="source_read_error",
                        message="Injected source could not process one module.",
                        path="b.py",
                    ),
                ),
            )

    result = analyse(tmp_path, fact_source=InjectedFactSource())

    assert result.complete is False
    assert result.import_facts[0].id.startswith("fact-")
    assert len(result.import_facts[0].id) == len("fact-") + 12
    assert result.dependencies[0].evidence[0].fact_id == result.import_facts[0].id
