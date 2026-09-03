from __future__ import annotations

import os
from pathlib import Path

import pytest

from pyarchgraph.discovery import discover_modules
from pyarchgraph.model import Severity, SourceModule


def _write_files(root: Path, paths: list[str]) -> None:
    for relative_path in paths:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")


def test_maps_modules_packages_main_modules_and_namespace_prefixes(
    tmp_path: Path,
) -> None:
    _write_files(
        tmp_path,
        [
            "top.py",
            "acme/__init__.py",
            "acme/api.py",
            "acme/cli/__main__.py",
            "namespace/deep/tool.py",
        ],
    )

    result = discover_modules(tmp_path)

    assert result.modules == (
        SourceModule("acme", "acme/__init__.py", True, None),
        SourceModule("acme.api", "acme/api.py", False, "acme"),
        SourceModule("acme.cli.__main__", "acme/cli/__main__.py", False, "acme.cli"),
        SourceModule(
            "namespace.deep.tool",
            "namespace/deep/tool.py",
            False,
            "namespace.deep",
        ),
        SourceModule("top", "top.py", False, None),
    )
    assert result.namespace_prefixes == (
        "acme.cli",
        "namespace",
        "namespace.deep",
    )
    assert result.diagnostics == ()


def test_default_directory_exclusions_apply_at_every_depth_but_tests_remain(
    tmp_path: Path,
) -> None:
    _write_files(
        tmp_path,
        [
            "keep.py",
            "tests/test_keep.py",
            ".git/ignored.py",
            ".venv/ignored.py",
            "venv/ignored.py",
            "__pycache__/ignored.py",
            "build/ignored.py",
            "dist/ignored.py",
            "pkg/build/also_ignored.py",
        ],
    )

    result = discover_modules(tmp_path)

    assert tuple(module.id for module in result.modules) == ("keep", "tests.test_keep")
    assert result.namespace_prefixes == ("tests",)
    assert result.diagnostics == ()


def test_user_exclusion_globs_are_or_combined_for_directories_and_files(
    tmp_path: Path,
) -> None:
    _write_files(
        tmp_path,
        [
            "pkg/keep.py",
            "pkg/generated/drop.py",
            "pkg/drop_generated.py",
            "other/drop_generated.py",
            "other/keep.py",
        ],
    )

    result = discover_modules(
        tmp_path,
        excludes=("pkg/generated", "*_generated.py"),
    )

    assert tuple(module.id for module in result.modules) == (
        "other.keep",
        "pkg.keep",
    )
    assert result.namespace_prefixes == ("other", "pkg")


@pytest.mark.parametrize("pattern", ["", "/absolute/**"])
def test_invalid_exclusion_patterns_fail_before_discovery(
    tmp_path: Path, pattern: str
) -> None:
    with pytest.raises(ValueError):
        discover_modules(tmp_path, excludes=(pattern,))


def test_invalid_module_paths_and_root_init_are_diagnosed_and_omitted(
    tmp_path: Path,
) -> None:
    _write_files(
        tmp_path,
        [
            "__init__.py",
            "valid.py",
            "bad-name.py",
            "class.py",
            "bad-dir/child.py",
        ],
    )

    result = discover_modules(tmp_path)

    assert tuple(module.id for module in result.modules) == ("valid",)
    assert [(item.code, item.path) for item in result.diagnostics] == [
        ("invalid_module_id", "bad-dir/child.py"),
        ("invalid_module_id", "bad-name.py"),
        ("invalid_module_id", "class.py"),
        ("root_init_unsupported", "__init__.py"),
    ]
    assert all(item.severity is Severity.ERROR for item in result.diagnostics)
    assert all(str(tmp_path) not in item.message for item in result.diagnostics)


def test_duplicate_module_id_excludes_the_overlapping_ambiguous_group(
    tmp_path: Path,
) -> None:
    _write_files(
        tmp_path,
        [
            "safe.py",
            "thing.py",
            "thing/__init__.py",
            "thing/child.py",
        ],
    )

    result = discover_modules(tmp_path)

    assert tuple(module.id for module in result.modules) == ("safe",)
    assert [(item.code, item.path) for item in result.diagnostics] == [
        ("duplicate_module_id", "thing.py"),
        ("non_package_prefix_conflict", "thing.py"),
    ]


def test_non_package_prefix_conflict_excludes_prefix_and_all_descendants(
    tmp_path: Path,
) -> None:
    _write_files(
        tmp_path,
        [
            "a.py",
            "a/child.py",
            "a/deep/grandchild.py",
            "unrelated.py",
        ],
    )

    result = discover_modules(tmp_path)

    assert tuple(module.id for module in result.modules) == ("unrelated",)
    assert [(item.code, item.path) for item in result.diagnostics] == [
        ("non_package_prefix_conflict", "a.py"),
    ]
    assert "a/child.py" in result.diagnostics[0].message
    assert "a/deep/grandchild.py" in result.diagnostics[0].message


def test_symlinked_directories_are_not_followed(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    outside = tmp_path / "outside"
    _write_files(source_root, ["real.py"])
    _write_files(outside, ["hidden.py"])
    (source_root / "linked").symlink_to(outside, target_is_directory=True)

    result = discover_modules(source_root)

    assert tuple(module.id for module in result.modules) == ("real",)
    assert result.namespace_prefixes == ()
    assert result.diagnostics == ()


def test_result_order_is_independent_of_filesystem_walk_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_files(
        tmp_path,
        [
            "z.py",
            "ns/b.py",
            "pkg/__init__.py",
            "pkg/a.py",
            "bad-name.py",
        ],
    )
    expected = discover_modules(tmp_path)
    walk_entries = [
        (directory, list(directory_names), list(file_names))
        for directory, directory_names, file_names in os.walk(tmp_path)
    ]

    def reversed_walk(*args: object, **kwargs: object):
        del args, kwargs
        for directory, directory_names, file_names in reversed(walk_entries):
            yield directory, list(reversed(directory_names)), list(reversed(file_names))

    monkeypatch.setattr("pyarchgraph.discovery.os.walk", reversed_walk)

    assert discover_modules(tmp_path) == expected
