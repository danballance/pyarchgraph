"""Exercise the real installed CLI without importing target applications."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pyarchgraph", str(root)],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )


def test_real_cli_reports_representative_concerns_without_execution_or_artifacts(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(
        tmp_path,
        "pkg/a.py",
        "import pkg.b\nimport pkg.b\nimport pkg.missing\nfrom importlib import import_module\nimport_module('pkg.dynamic')\n",
    )
    _write(tmp_path, "pkg/b.py", "import pkg.a\n")
    _write(tmp_path, "pkg/dynamic.py", "")
    _write(tmp_path, "pkg/self.py", "import pkg.self\n")
    _write(
        tmp_path,
        "pkg/never_execute.py",
        "from pathlib import Path\nPath(__file__).with_name('EXECUTED').write_text('bad')\nraise RuntimeError('must never execute')\n",
    )
    _write(tmp_path, "tests/test_broken.py", "not ! valid python")
    original_files = set(tmp_path.rglob("*"))
    first = _run(tmp_path)
    second = _run(tmp_path)
    assert first.returncode == second.returncode == 1
    assert first.stderr == second.stderr == ""
    assert first.stdout == second.stdout
    document = json.loads(first.stdout)
    assert document["module_count"] == 6
    assert document["dependency_count"] == 3
    assert sorted(f["kind"] for f in document["findings"]) == [
        "cycle",
        "cycle",
        "unresolved_import",
    ]
    assert set(tmp_path.rglob("*")) == original_files
    assert not (tmp_path / "pkg/EXECUTED").exists()


@pytest.mark.parametrize("bad_source", [b"def nope(:\n", b"# coding: utf-8\n\xff\n"])
def test_real_cli_rejects_partial_analysis_even_when_a_cycle_was_found(
    tmp_path: Path, bad_source: bytes
) -> None:
    _write(tmp_path, "a.py", "import b\n")
    _write(tmp_path, "b.py", "import a\n")
    (tmp_path / "broken.py").write_bytes(bad_source)
    completed = _run(tmp_path)
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "broken.py" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_equivalent_import_spellings_are_normalized(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/__init__.py", "")
    _write(tmp_path, "pkg/a.py", "import pkg.b\n")
    _write(tmp_path, "pkg/b.py", "")
    absolute = json.loads(_run(tmp_path).stdout)
    _write(tmp_path, "pkg/a.py", "from . import b\n")
    relative = json.loads(_run(tmp_path).stdout)
    assert (
        absolute
        == relative
        == {
            "schema_version": "0.5",
            "module_count": 3,
            "dependency_count": 1,
            "findings": [],
        }
    )
