"""Exercise the real import-linter contracts with isolated nested packages."""

from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]


def lint_fixture(
    tmp_path: Path, extra_files: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    files = {
        "__main__.py": "import pyarchgraph.main\n",
        "main.py": "import pyarchgraph.adapters.driving.cli.application\n",
        "domain/models.py": "",
        "application/requests.py": "import pyarchgraph.domain.models\n",
        "adapters/driving/cli/application.py": "import pyarchgraph.application.requests\n",
        "adapters/driven/filesystem/discovery.py": "",
        "adapters/driven/filesystem/project.py": "",
        "adapters/driven/python_ast.py": "",
        "adapters/driven/networkx_graph.py": "import networkx\n",
    }
    files.update(extra_files)
    package = tmp_path / "pyarchgraph"
    for relative_path, source in files.items():
        path = package / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        parent = path.parent
        while parent != tmp_path:
            (parent / "__init__.py").touch()
            parent = parent.parent
        path.write_text(source, encoding="utf-8")
    configuration = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    configuration = (
        "[tool.importlinter]" + configuration.split("[tool.importlinter]", 1)[1]
    )
    (tmp_path / "pyproject.toml").write_text(configuration, encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from importlinter.cli import lint_imports_command; lint_imports_command()",
            "--no-cache",
            "--no-logo",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("path", "source", "contract"),
    [
        (
            "domain/policies/nested.py",
            "import pyarchgraph.application.requests\n",
            "Runtime dependencies point inward",
        ),
        (
            "application/use_cases/nested.py",
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n"
            "    import pyarchgraph.adapters.driven.python_ast\n",
            "Runtime dependencies point inward",
        ),
        (
            "adapters/driven/filesystem/nested.py",
            "import pyarchgraph.main\n",
            "Runtime dependencies point inward",
        ),
        (
            "adapters/driving/cli/nested.py",
            "import pyarchgraph.adapters.driven.python_ast\n",
            "Driving and driven adapters are independent",
        ),
        (
            "adapters/driven/filesystem/nested.py",
            "import pyarchgraph.adapters.driven.python_ast\n",
            "Driven adapter components are independent",
        ),
        (
            "application/ports/nested.py",
            "import networkx\n",
            "The core does not depend on external frameworks",
        ),
    ],
)
def test_contracts_reject_nested_violations(
    tmp_path: Path, path: str, source: str, contract: str
) -> None:
    result = lint_fixture(tmp_path, {path: source})
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert f"{contract} BROKEN" in output, output


def test_contracts_allow_inward_and_same_component_imports(tmp_path: Path) -> None:
    result = lint_fixture(
        tmp_path,
        {
            "adapters/driven/filesystem/discovery.py": "import pyarchgraph.adapters.driven.filesystem.project\n"
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
