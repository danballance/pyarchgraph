from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyarchgraph.cli import main


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
        ({"a.py": "__import__('external')\n"}, 1),
    ],
)
def test_complete_analysis_prints_only_json_and_generates_no_files(
    tmp_path: Path, capsys, files: dict[str, str], expected: int
) -> None:
    _write(tmp_path, files)
    before = set(tmp_path.rglob("*"))
    assert main([str(tmp_path)]) == expected
    output = capsys.readouterr()
    document = json.loads(output.out)
    assert document["schema_version"] == "0.5"
    assert bool(document["findings"]) == bool(expected)
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
def test_analysis_errors_return_two_without_partial_json(
    tmp_path: Path, capsys, files: dict[str, str]
) -> None:
    _write(tmp_path, files)
    assert main([str(tmp_path)]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err
    assert "Traceback" not in output.err


def test_nonexistent_source_is_an_error(tmp_path: Path, capsys) -> None:
    assert main([str(tmp_path / "missing")]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err


def test_file_source_root_is_an_error(tmp_path: Path, capsys) -> None:
    _write(tmp_path, {"a.py": ""})
    assert main([str(tmp_path / "a.py")]) == 2
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
    assert main([str(tmp_path), "--exclude", "bad.py", "--exclude", "generated"]) == 0
    assert json.loads(capsys.readouterr().out)["module_count"] == 1


def test_boundary_options_are_repeatable_and_combine_matching_rules(
    tmp_path: Path, capsys
) -> None:
    _write(tmp_path, {"a.py": "import b\n", "b.py": ""})
    assert main([str(tmp_path), "--forbid", "a:b", "--forbid", "*:b"]) == 1
    (finding,) = json.loads(capsys.readouterr().out)["findings"]
    assert finding["rules"] == [["*", "b"], ["a", "b"]]


@pytest.mark.parametrize("rule", ["", "a", ":b", "a:", "a:b:c"])
def test_invalid_boundary_rule_is_an_argument_error(
    tmp_path: Path, capsys, rule: str
) -> None:
    _write(tmp_path, {"a.py": ""})
    with pytest.raises(SystemExit) as error:
        main([str(tmp_path), "--forbid", rule])
    assert error.value.code == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err


@pytest.mark.parametrize(
    "flag",
    [
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
        main([str(tmp_path), flag])
    assert error.value.code == 2
    assert capsys.readouterr().out == ""


def test_source_root_is_required(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        main([])
    assert error.value.code == 2
    assert capsys.readouterr().out == ""


def test_help_describes_only_the_small_cli(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        main(["--help"])
    assert error.value.code == 0
    help_text = capsys.readouterr().out
    assert "--exclude" in help_text and "--forbid" in help_text
    assert "--baseline" not in help_text and "--output" not in help_text
