from __future__ import annotations

import json
import os
from pathlib import Path
import random
import subprocess
import sys

import pytest

from pyarchgraph.discovery import (
    _Candidate,
    _remove_ambiguous_groups,
    discover_modules,
)
from pyarchgraph.model import Diagnostic, Severity, SourceModule


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


def _pairwise_conflict_reference(
    candidates: list[_Candidate],
) -> tuple[tuple[_Candidate, ...], tuple[Diagnostic, ...]]:
    """Independent all-pairs reference for inventory and diagnostic semantics."""

    ordered = sorted(candidates, key=lambda item: (item.module.id, item.module.path))
    groups: dict[str, list[_Candidate]] = {}
    for candidate in ordered:
        groups.setdefault(candidate.module.id, []).append(candidate)
    excluded: set[_Candidate] = set()
    duplicates = []
    prefixes = []
    for module_id, group in groups.items():
        if len(group) > 1:
            excluded.update(group)
            paths = tuple(item.module.path for item in group)
            duplicates.append(
                Diagnostic(
                    Severity.ERROR,
                    "duplicate_module_id",
                    f"Module ID {module_id!r} is produced by multiple source "
                    f"files: {', '.join(paths)}.",
                    path=paths[0],
                )
            )
        non_packages = [item for item in group if not item.module.is_package]
        descendants = [
            item for item in ordered if item.module.id.startswith(module_id + ".")
        ]
        if non_packages and descendants:
            excluded.update(non_packages)
            excluded.update(descendants)
            paths = sorted(item.module.path for item in descendants)
            prefixes.append(
                Diagnostic(
                    Severity.ERROR,
                    "non_package_prefix_conflict",
                    f"Non-package module {module_id!r} cannot prefix descendant "
                    f"modules from: {', '.join(paths)}.",
                    path=non_packages[0].module.path,
                )
            )
    return (
        tuple(item for item in ordered if item not in excluded),
        tuple(duplicates + prefixes),
    )


def test_prefix_index_matches_pairwise_reference_for_generated_inventories() -> None:
    rng = random.Random(7429)
    names = ("a", "a.b", "a.b.c", "a.c", "aa", "aa.b", "b", "b.c", "z")
    for _ in range(200):
        candidates = []
        for index in range(rng.randrange(1, 35)):
            name = rng.choice(names)
            candidates.append(
                _Candidate(
                    SourceModule(
                        name,
                        f"source{index:02d}/{name.replace('.', '/')}.py",
                        bool(rng.randrange(2)),
                        name.rpartition(".")[0] or None,
                    )
                )
            )
        expected = _pairwise_conflict_reference(candidates)
        assert _remove_ambiguous_groups(candidates) == expected
        assert _remove_ambiguous_groups(reversed(candidates)) == expected


def test_duplicate_and_nested_prefix_conflicts_remove_transitive_group(
    tmp_path: Path,
) -> None:
    _write_files(
        tmp_path,
        [
            "a.py",
            "a/__init__.py",
            "a/b.py",
            "a/b/__init__.py",
            "a/b/child.py",
            "a/sibling.py",
            "aa.py",
            "safe/__init__.py",
            "safe/child.py",
        ],
    )

    result = discover_modules(tmp_path)

    assert tuple(module.id for module in result.modules) == ("aa", "safe", "safe.child")
    assert [(item.code, item.path) for item in result.diagnostics] == [
        ("duplicate_module_id", "a.py"),
        ("duplicate_module_id", "a/b.py"),
        ("non_package_prefix_conflict", "a.py"),
        ("non_package_prefix_conflict", "a/b.py"),
    ]
    assert (
        "a/b.py, a/b/__init__.py, a/b/child.py, a/sibling.py"
        in result.diagnostics[2].message
    )


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO fixture")
def test_non_regular_source_is_diagnosed_and_omitted(tmp_path: Path) -> None:
    _write_files(tmp_path, ["ordinary.py"])
    os.mkfifo(tmp_path / "pipe.py")

    result = discover_modules(tmp_path)

    assert tuple(module.id for module in result.modules) == ("ordinary",)
    assert [(item.code, item.path, item.severity) for item in result.diagnostics] == [
        ("source_not_regular", "pipe.py", Severity.ERROR),
    ]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO fixture")
def test_fifo_cli_exits_promptly_with_incomplete_report(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _write_files(source, ["ordinary.py"])
    os.mkfifo(source / "pipe.py")
    output = tmp_path / "output"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pyarchgraph",
            str(source),
            "--json-only",
            "--output-dir",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert completed.returncode == 1
    document = json.loads((output / "dependency-graph.json").read_text())
    assert document["analysis"]["complete"] is False
    assert document["quality"]["score"] is None
    assert [item["code"] for item in document["diagnostics"]] == ["source_not_regular"]


def test_regular_file_symlinks_are_retained(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_files(source, ["ordinary.py"])
    target = tmp_path / "target.py"
    target.write_text("", encoding="utf-8")
    try:
        (source / "linked.py").symlink_to(target)
    except (NotImplementedError, OSError):
        pytest.skip("file symlinks are unavailable")

    result = discover_modules(source)

    assert tuple(module.id for module in result.modules) == ("linked", "ordinary")
    assert result.diagnostics == ()


def test_disappearing_source_remains_an_explicit_read_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "pyarchgraph.discovery.os.walk",
        lambda *args, **kwargs: [(str(tmp_path), [], ["missing.py"])],
    )

    result = discover_modules(tmp_path)

    assert result.modules == ()
    assert [(item.code, item.path, item.severity) for item in result.diagnostics] == [
        ("source_read_error", "missing.py", Severity.ERROR),
    ]
