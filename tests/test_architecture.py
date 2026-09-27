"""Enforce the runtime's inward imports and separation from external systems."""

import ast
from pathlib import Path
import sys

import pytest


PACKAGE = Path(__file__).resolve().parents[1] / "pyarchgraph"
RUNTIME = tuple(sorted(PACKAGE.rglob("*.py")))
CORE = tuple(path for path in RUNTIME if path.parent.name in {"domain", "application"})


@pytest.mark.parametrize(
    "path", RUNTIME, ids=lambda path: str(path.relative_to(PACKAGE))
)
def test_runtime_imports_obey_package_boundaries(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    layer = path.parent.name
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = tuple(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert not node.level, f"{path}: use defining-module absolute imports"
            modules = (node.module or "",)
        else:
            continue
        for module in modules:
            if module.startswith("pyarchgraph"):
                if path.name != "__init__.py":
                    assert module not in {
                        "pyarchgraph",
                        "pyarchgraph.domain",
                        "pyarchgraph.application",
                        "pyarchgraph.adapters",
                    }, f"{path}: internal code must bypass package facades"
                if layer == "domain":
                    assert module.startswith("pyarchgraph.domain."), (path, module)
                elif layer == "application":
                    assert module.startswith(
                        ("pyarchgraph.domain.", "pyarchgraph.application.")
                    ), (path, module)
                elif layer == "adapters":
                    assert module != "pyarchgraph.composition", (path, module)
            elif layer in {"domain", "application"}:
                assert module.partition(".")[0] in sys.stdlib_module_names, (
                    path,
                    module,
                )


@pytest.mark.parametrize("path", CORE, ids=lambda path: str(path.relative_to(PACKAGE)))
def test_core_does_not_access_external_systems(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
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
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported = {alias.name.partition(".")[0] for alias in node.names}
            assert not imported & forbidden_imports, (path, imported)
        elif isinstance(node, ast.ImportFrom):
            assert (node.module or "").partition(".")[0] not in forbidden_imports, (
                path,
                node.module,
            )
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                assert node.func.id not in {"open", "print", "input", "__import__"}, (
                    path,
                    node.lineno,
                )
            elif isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden_methods, (path, node.lineno)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            assert (node.value.id, node.attr) not in {
                ("sys", "stdout"),
                ("sys", "stderr"),
                ("sys", "stdin"),
            }, (path, node.lineno)


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


def test_runtime_has_only_one_package_layer() -> None:
    assert all(len(path.relative_to(PACKAGE).parts) <= 2 for path in RUNTIME)
