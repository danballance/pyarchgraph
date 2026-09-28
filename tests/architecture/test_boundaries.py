"""Check recursive runtime boundaries, core purity and repository conventions."""

import ast
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "pyarchgraph"
RUNTIME = tuple(sorted(PACKAGE.rglob("*.py")))
CORE = tuple(
    path
    for path in RUNTIME
    if path.relative_to(PACKAGE).parts[0] in {"domain", "application"}
)
PACKAGES = {
    ".".join(("pyarchgraph", *path.relative_to(PACKAGE).parts[:-1]))
    for path in RUNTIME
    if path.name == "__init__.py"
}


def import_violations(relative_path: str, source: str) -> list[str]:
    parts = Path(relative_path).parts
    layer = parts[0]
    component = parts[1:3] if layer == "adapters" else ()
    violations = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                violations.append("use absolute defining-module imports")
            modules = (node.module or "",)
        else:
            continue
        for module in modules:
            if module == "pyarchgraph" or module.startswith("pyarchgraph."):
                if module in PACKAGES:
                    violations.append(
                        "import the defining module, not a package facade"
                    )
                allowed = {
                    "domain": ("pyarchgraph.domain.",),
                    "application": ("pyarchgraph.domain.", "pyarchgraph.application."),
                }.get(layer)
                if allowed and not module.startswith(allowed):
                    violations.append("core dependencies must point inward")
                if layer == "adapters":
                    if module in {"pyarchgraph.main", "pyarchgraph.__main__"}:
                        violations.append("adapters cannot import the composition root")
                    if module.startswith("pyarchgraph.adapters."):
                        target = tuple(module.split(".")[2:4])
                        if target != component:
                            violations.append(
                                "separate adapter components must be independent"
                            )
            elif layer in {"domain", "application"}:
                if module.partition(".")[0] not in sys.stdlib_module_names:
                    violations.append("core dependencies must be standard library only")
    return violations


def external_system_violations(source: str) -> list[str]:
    forbidden_imports = {
        "argparse",
        "ast",
        "configparser",
        "glob",
        "io",
        "networkx",
        "os",
        "shutil",
        "socket",
        "subprocess",
        "tokenize",
        "tomllib",
    }
    forbidden_methods = {
        "chdir",
        "cwd",
        "exists",
        "expanduser",
        "glob",
        "is_dir",
        "is_file",
        "is_symlink",
        "iterdir",
        "lstat",
        "mkdir",
        "open",
        "read_bytes",
        "read_text",
        "readlink",
        "rglob",
        "stat",
        "unlink",
        "walk",
        "write_bytes",
        "write_text",
        "import_module",
        "find_spec",
        "module_from_spec",
        "exec_module",
        "load_module",
        "spec_from_file_location",
        "spec_from_loader",
    }
    violations = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            if {
                alias.name.partition(".")[0] for alias in node.names
            } & forbidden_imports:
                violations.append("core cannot import external-system machinery")
            if any(
                alias.name.partition(".")[0] == "importlib"
                and alias.name != "importlib.util"
                for alias in node.names
            ):
                violations.append(
                    "core may use importlib only for lexical name resolution"
                )
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").partition(".")[0] in forbidden_imports:
                violations.append("core cannot import external-system machinery")
            if (node.module or "").partition(".")[0] == "importlib" and (
                node.module != "importlib.util"
                or any(alias.name != "resolve_name" for alias in node.names)
            ):
                violations.append(
                    "core may use importlib only for lexical name resolution"
                )
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in {
                "open",
                "print",
                "input",
                "__import__",
            }:
                violations.append("core cannot perform external-system operations")
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in forbidden_methods
            ):
                violations.append("core cannot perform external-system operations")
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if (node.value.id, node.attr) in {
                ("sys", "stdout"),
                ("sys", "stderr"),
                ("sys", "stdin"),
            }:
                violations.append("core cannot access process streams")
    return violations


@pytest.mark.parametrize(
    "path", RUNTIME, ids=lambda path: str(path.relative_to(PACKAGE))
)
def test_runtime_imports_obey_package_boundaries(path: Path) -> None:
    assert not import_violations(
        str(path.relative_to(PACKAGE)), path.read_text(encoding="utf-8")
    )


@pytest.mark.parametrize("path", CORE, ids=lambda path: str(path.relative_to(PACKAGE)))
def test_core_does_not_access_external_systems(path: Path) -> None:
    assert not external_system_violations(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("path", "source", "violation"),
    [
        (
            "domain/policies/nested.py",
            "import pyarchgraph.application.requests",
            "point inward",
        ),
        ("application/ports/nested.py", "import httpx", "standard library only"),
        (
            "application/use_cases/nested.py",
            "from ...domain.models import Severity",
            "absolute",
        ),
        (
            "application/use_cases/nested.py",
            "from pyarchgraph.domain import Severity",
            "facade",
        ),
        (
            "adapters/driven/filesystem/nested.py",
            "import pyarchgraph.main",
            "composition root",
        ),
        (
            "adapters/driven/filesystem/nested.py",
            "import pyarchgraph.adapters.driven.python_ast",
            "independent",
        ),
        (
            "application/use_cases/nested.py",
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n"
            "    import pyarchgraph.adapters.driven.python_ast\n",
            "point inward",
        ),
    ],
)
def test_nested_import_violations_are_rejected(
    path: str, source: str, violation: str
) -> None:
    assert any(violation in item for item in import_violations(path, source))


@pytest.mark.parametrize(
    "source",
    [
        "from pathlib import Path\nPath('source.py').read_text()",
        "open('source.py')",
        "import subprocess",
        "from importlib import import_module as load",
        "from importlib.util import find_spec as lookup",
        "import importlib.util\nimportlib.util.find_spec('networkx')",
    ],
)
def test_external_system_operations_are_rejected(source: str) -> None:
    assert external_system_violations(source)


def test_lexical_import_name_resolution_is_allowed() -> None:
    assert not external_system_violations(
        "import importlib.util\nimportlib.util.resolve_name('.models', 'pyarchgraph.domain')"
    )
    assert not external_system_violations(
        "from importlib.util import resolve_name\nresolve_name('.models', 'pyarchgraph.domain')"
    )


def test_helpers_inside_one_adapter_component_can_collaborate() -> None:
    assert not import_violations(
        "adapters/driving/cli/application.py",
        "from pyarchgraph.adapters.driving.cli.rendering import JsonReportRenderer",
    )
    assert not import_violations(
        "adapters/driven/filesystem/discovery.py",
        "from pyarchgraph.adapters.driven.filesystem.project import FileSystemProjectAccess",
    )


def test_runtime_behavior_is_organized_in_classes() -> None:
    for path in RUNTIME:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        functions = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        assert functions == (["main"] if path == PACKAGE / "__main__.py" else []), (
            path,
            functions,
        )
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if path == PACKAGE / "__main__.py" and node.name == "main":
                    continue
                assert isinstance(parents[node], ast.ClassDef), (path, node.lineno)


def test_package_initializers_are_inert() -> None:
    for path in RUNTIME:
        if path.name != "__init__.py":
            continue
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                assert isinstance(node.value.value, str), path
                continue
            assert path == PACKAGE / "__init__.py" and isinstance(node, ast.Assign), (
                path
            )
            assert len(node.targets) == 1 and isinstance(node.targets[0], ast.Name), (
                path
            )
            assert node.targets[0].id == "__version__", path
            assert isinstance(node.value, ast.Constant) and isinstance(
                node.value.value, str
            ), path


def test_core_cold_imports_do_not_load_adapters_or_networkx() -> None:
    modules = ["pyarchgraph"] + [
        ".".join(("pyarchgraph", *path.relative_to(PACKAGE).with_suffix("").parts))
        for path in CORE
        if path.name != "__init__.py"
    ]
    script = """
import importlib
import importlib.abc
import sys

class BlockNetworkX(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'networkx' or fullname.startswith('networkx.'):
            raise AssertionError('core imports must not load NetworkX')
        return None

sys.meta_path.insert(0, BlockNetworkX())
for name in sys.argv[1:]:
    importlib.import_module(name)
assert not any(name == 'networkx' or name.startswith('networkx.') for name in sys.modules)
assert not any(name == 'pyarchgraph.adapters' or name.startswith('pyarchgraph.adapters.') for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, *modules],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
