from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyarchgraph.main import ApplicationFactory


def _write(root: Path, files: dict[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


@pytest.mark.parametrize(
    "files,expected",
    [
        ({"a.py": "import b\n", "b.py": ""}, 0),
        ({"a.py": "import b\n", "b.py": "import a\n"}, 1),
        ({"a.py": "__import__('external')\n"}, 0),
    ],
)
def test_complete_analysis_prints_only_json_and_generates_no_files(
    tmp_path: Path, capsys, files: dict[str, str], expected: int
) -> None:
    _write(tmp_path, files)
    before = set(tmp_path.rglob("*"))
    assert ApplicationFactory().create_cli().run([str(tmp_path)]) == expected
    output = capsys.readouterr()
    document = json.loads(output.out)
    assert document["schema_version"] == "0.8"
    assert bool(document["views"]["structural"]["findings"]) == bool(expected)
    assert output.err == ""
    assert output.out.endswith("\n")
    assert set(tmp_path.rglob("*")) == before


@pytest.mark.parametrize(
    "files",
    [
        {},
        {"__init__.py": ""},
        {"a.py": "def nope(:\n"},
        {"a.py": "import b\n", "b.py": "import a\n", "broken.py": "def nope(:\n"},
    ],
)
def test_analysis_errors_return_two_with_partial_json(
    tmp_path: Path, capsys, files: dict[str, str]
) -> None:
    _write(tmp_path, files)
    assert ApplicationFactory().create_cli().run([str(tmp_path)]) == 2
    output = capsys.readouterr()
    assert json.loads(output.out)["status"] == "incomplete"
    assert output.err == ""


def test_nonexistent_source_is_an_error(tmp_path: Path, capsys) -> None:
    assert ApplicationFactory().create_cli().run([str(tmp_path / "missing")]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err


def test_file_source_root_is_an_error(tmp_path: Path, capsys) -> None:
    _write(tmp_path, {"a.py": ""})
    assert ApplicationFactory().create_cli().run([str(tmp_path / "a.py")]) == 2
    assert capsys.readouterr().out == ""


def test_exclusions_are_repeatable_and_applied_before_parsing(
    tmp_path: Path, capsys
) -> None:
    _write(
        tmp_path,
        {
            "a.py": "",
            "bad.py": "invalid ! source",
            "generated/bad.py": "invalid ! source",
        },
    )
    assert (
        ApplicationFactory()
        .create_cli()
        .run([str(tmp_path), "--exclude", "bad.py", "--exclude", "generated"])
        == 0
    )
    assert len(json.loads(capsys.readouterr().out)["sources"]) == 1


@pytest.mark.parametrize(
    "flag",
    [
        "--forbid",
        "--output-dir",
        "--output",
        "--json-only",
        "--check",
        "--baseline",
        "--cleanup-baseline",
        "--allow-inventory-change",
        "--view",
        "--package-depth",
        "--implied-edges",
        "--exclude-type-only",
        "--exclude-local",
        "--include-tests",
        "--expect-package",
        "--project-root",
    ],
)
def test_removed_options_are_rejected(tmp_path: Path, capsys, flag: str) -> None:
    _write(tmp_path, {"a.py": ""})
    with pytest.raises(SystemExit) as error:
        ApplicationFactory().create_cli().run([str(tmp_path), flag])
    assert error.value.code == 2
    assert capsys.readouterr().out == ""


def test_source_root_is_required(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        ApplicationFactory().create_cli().run([])
    assert error.value.code == 2
    assert capsys.readouterr().out == ""


def test_help_describes_only_the_small_cli(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        ApplicationFactory().create_cli().run(["--help"])
    assert error.value.code == 0
    help_text = capsys.readouterr().out
    assert "--exclude" in help_text
    assert "--forbid" not in help_text
    assert "--baseline" not in help_text and "--output" not in help_text
