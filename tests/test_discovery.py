from __future__ import annotations

import json
import os
import random
import subprocess
import sys
from pathlib import Path

import pytest

from pyarchgraph.adapters.discovery import FileSystemSourceDiscovery
from pyarchgraph.domain.model import ExcludedPath, Severity, SourceModule


def _write_files(root: Path, paths: list[str]) -> None:
    for relative_path in paths:
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")


def test_maps_modules_packages_main_modules_and_namespace_prefixes(
    tmp_path: Path,
) -> None:
    paths = [
        "top.py",
        "acme/__init__.py",
        "acme/api.py",
        "acme/cli/__main__.py",
        "namespace/deep/tool.py",
    ]
    _write_files(tmp_path, paths)
    result = FileSystemSourceDiscovery().discover(tmp_path)
    assert [
        (item.id, item.import_name, item.is_package, item.parent_package)
        for item in result.modules
    ] == [
        ("source:acme/__init__.py", "acme", True, None),
        ("source:acme/api.py", "acme.api", False, "acme"),
        ("source:acme/cli/__main__.py", "acme.cli.__main__", False, "acme.cli"),
        (
            "source:namespace/deep/tool.py",
            "namespace.deep.tool",
            False,
            "namespace.deep",
        ),
        ("source:top.py", "top", False, None),
    ]
    assert result.namespace_prefixes == ("acme.cli", "namespace", "namespace.deep")
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
    result = FileSystemSourceDiscovery().discover(tmp_path)
    assert [module.import_name for module in result.modules] == [
        "keep",
        "tests.test_keep",
    ]
    assert result.namespace_prefixes == ("pkg", "tests")
    assert {item.path for item in result.excluded_paths} == {
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        "build",
        "dist",
        "pkg/build",
    }
    assert all(item.kind == "directory" for item in result.excluded_paths)
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
    result = FileSystemSourceDiscovery().discover(
        tmp_path, excludes=("pkg/generated", "*_generated.py")
    )
    assert [module.import_name for module in result.modules] == [
        "other.keep",
        "pkg.keep",
    ]
    assert result.namespace_prefixes == ("other", "pkg")
    assert result.excluded_paths == (
        ExcludedPath("other/drop_generated.py", "*_generated.py", "file"),
        ExcludedPath("pkg/drop_generated.py", "*_generated.py", "file"),
        ExcludedPath("pkg/generated", "pkg/generated", "directory"),
    )
    assert {
        (target.name, target.path, target.acknowledged) for target in result.targets
    } == {
        ("other.drop_generated", "other/drop_generated.py", True),
        ("pkg.drop_generated", "pkg/drop_generated.py", True),
        ("pkg.generated", "pkg/generated/", True),
    }


@pytest.mark.parametrize("pattern", ["", "/absolute/**"])
def test_invalid_exclusion_patterns_fail_before_discovery(
    tmp_path: Path, pattern: str
) -> None:
    with pytest.raises(ValueError):
        FileSystemSourceDiscovery().discover(tmp_path, excludes=(pattern,))


def test_unusual_names_keep_lossless_identities_and_other_sources_keep_paths(
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
            "pkg/0001_initial.py",
            "pkg/is/formats.py",
            ".hidden/script.py",
            "foo.bar.py",
        ],
    )
    result = FileSystemSourceDiscovery().discover(tmp_path)
    by_path = {module.path: module for module in result.modules}
    assert len(by_path) == 9
    for path in (
        "bad-name.py",
        "class.py",
        "bad-dir/child.py",
        "pkg/0001_initial.py",
        "pkg/is/formats.py",
    ):
        assert by_path[path].import_name == path[:-3].replace("/", ".")
        assert by_path[path].binding_status == "bound"
    for path in ("__init__.py", ".hidden/script.py", "foo.bar.py"):
        assert by_path[path].import_name is None
        assert by_path[path].binding_status == "path_only"
    assert [(item.code, item.path) for item in result.diagnostics] == [
        ("root_init_unsupported", "__init__.py"),
        ("path_only_source", ".hidden/script.py"),
        ("path_only_source", "foo.bar.py"),
    ]
    assert result.diagnostics[0].severity is Severity.ERROR
    assert all(str(tmp_path) not in item.message for item in result.diagnostics)


def test_package_precedence_keeps_shadowed_file_and_all_package_children(
    tmp_path: Path,
) -> None:
    _write_files(
        tmp_path, ["safe.py", "thing.py", "thing/__init__.py", "thing/child.py"]
    )
    result = FileSystemSourceDiscovery().discover(tmp_path)
    assert len(result.modules) == 4
    assert {module.path: module.binding_status for module in result.modules} == {
        "safe.py": "bound",
        "thing.py": "shadowed",
        "thing/__init__.py": "bound",
        "thing/child.py": "bound",
    }
    assert [(item.code, item.path, item.severity) for item in result.diagnostics] == [
        ("shadowed_source", "thing.py", Severity.INFO)
    ]


def test_non_package_prefix_keeps_sources_but_disables_descendant_bindings(
    tmp_path: Path,
) -> None:
    _write_files(
        tmp_path, ["a.py", "a/child.py", "a/deep/grandchild.py", "unrelated.py"]
    )
    result = FileSystemSourceDiscovery().discover(tmp_path)
    assert len(result.modules) == 4
    assert {module.path: module.binding_status for module in result.modules} == {
        "a.py": "bound",
        "a/child.py": "path_only",
        "a/deep/grandchild.py": "path_only",
        "unrelated.py": "bound",
    }
    assert [(item.code, item.path) for item in result.diagnostics] == [
        ("non_package_prefix_conflict", "a/child.py"),
        ("non_package_prefix_conflict", "a/deep/grandchild.py"),
    ]
    assert result.namespace_prefixes == ()


def test_symlinked_directories_are_not_followed(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    outside = tmp_path / "outside"
    _write_files(source_root, ["real.py"])
    _write_files(outside, ["hidden.py"])
    (source_root / "linked").symlink_to(outside, target_is_directory=True)
    result = FileSystemSourceDiscovery().discover(source_root)
    assert [module.import_name for module in result.modules] == ["real"]
    assert result.namespace_prefixes == ()
    assert result.excluded_paths == (
        ExcludedPath("linked", "directory_symlink", "directory"),
    )
    assert not result.targets[0].acknowledged
    assert result.diagnostics == ()


def test_result_order_is_independent_of_filesystem_walk_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_files(
        tmp_path, ["z.py", "ns/b.py", "pkg/__init__.py", "pkg/a.py", "bad-name.py"]
    )
    expected = FileSystemSourceDiscovery().discover(tmp_path)
    entries = [
        (directory, list(directories), list(files))
        for directory, directories, files in os.walk(tmp_path)
    ]

    def reversed_walk(*args: object, **kwargs: object):
        for directory, directories, files in reversed(entries):
            yield directory, list(reversed(directories)), list(reversed(files))

    monkeypatch.setattr("pyarchgraph.adapters.discovery.os.walk", reversed_walk)
    assert FileSystemSourceDiscovery().discover(tmp_path) == expected


def _pairwise_binding_reference(candidates: list[SourceModule]) -> dict[str, str]:
    """Independent all-pairs reference for binding precedence and blockers."""
    status = {}
    for candidate in candidates:
        peers = [
            item for item in candidates if item.import_name == candidate.import_name
        ]
        packages = [item for item in peers if item.is_package]
        if len(peers) == 1:
            status[candidate.id] = "bound"
        elif len(packages) == 1:
            status[candidate.id] = "bound" if candidate.is_package else "shadowed"
        else:
            status[candidate.id] = "ambiguous"
    blockers = [
        item
        for item in candidates
        if status[item.id] == "bound" and not item.is_package
    ]
    for candidate in candidates:
        if any(
            candidate.import_name.startswith(blocker.import_name + ".")
            for blocker in blockers
        ):
            status[candidate.id] = "path_only"
    return status


def test_prefix_index_matches_pairwise_reference_for_generated_inventories() -> None:
    rng = random.Random(7429)
    names = ("a", "a.b", "a.b.c", "a.c", "aa", "aa.b", "b", "b.c", "z")
    for _ in range(200):
        candidates = []
        for index in range(rng.randrange(1, 35)):
            name = rng.choice(names)
            candidates.append(
                SourceModule(
                    f"source:{index:02d}",
                    f"source{index:02d}/{name}.py",
                    bool(rng.randrange(2)),
                    name.rpartition(".")[0] or None,
                    import_name=name,
                )
            )
        expected = _pairwise_binding_reference(candidates)
        modules, diagnostics = FileSystemSourceDiscovery()._remove_ambiguous_groups(
            candidates
        )
        assert {module.id: module.binding_status for module in modules} == expected
        assert len(modules) == len(candidates)
        assert FileSystemSourceDiscovery()._remove_ambiguous_groups(
            reversed(candidates)
        ) == (modules, diagnostics)


def test_nested_package_precedence_does_not_shadow_other_descendants(
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
    result = FileSystemSourceDiscovery().discover(tmp_path)
    assert len(result.modules) == 9
    assert {
        module.path for module in result.modules if module.binding_status == "shadowed"
    } == {"a.py", "a/b.py"}
    assert all(
        module.binding_status in {"bound", "shadowed"} for module in result.modules
    )
    assert all(item.severity is Severity.INFO for item in result.diagnostics)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO fixture")
def test_non_regular_source_is_diagnosed_and_retained_in_inventory(
    tmp_path: Path,
) -> None:
    _write_files(tmp_path, ["ordinary.py"])
    os.mkfifo(tmp_path / "pipe.py")
    result = FileSystemSourceDiscovery().discover(tmp_path)
    assert [module.path for module in result.modules] == ["ordinary.py", "pipe.py"]
    assert [(item.code, item.path, item.severity) for item in result.diagnostics] == [
        ("source_not_regular", "pipe.py", Severity.ERROR)
    ]


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO fixture")
def test_fifo_cli_exits_promptly_with_partial_report(tmp_path: Path) -> None:
    _write_files(tmp_path, ["ordinary.py"])
    os.mkfifo(tmp_path / "pipe.py")
    completed = subprocess.run(
        [sys.executable, "-m", "pyarchgraph", str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert completed.returncode == 2
    report = json.loads(completed.stdout)
    assert report["status"] == "incomplete"
    assert any(
        item["code"] == "source_not_regular"
        for item in report["coverage"]["diagnostics"]
    )


def test_regular_file_symlinks_are_retained(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _write_files(source, ["ordinary.py"])
    target = tmp_path / "target.py"
    target.write_text("", encoding="utf-8")
    try:
        (source / "linked.py").symlink_to(target)
    except (NotImplementedError, OSError):
        pytest.skip("file symlinks are unavailable")
    result = FileSystemSourceDiscovery().discover(source)
    assert [module.import_name for module in result.modules] == ["linked", "ordinary"]
    assert result.diagnostics == ()


def test_disappearing_source_remains_in_inventory_with_read_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "pyarchgraph.adapters.discovery.os.walk",
        lambda *args, **kwargs: [(str(tmp_path), [], ["missing.py"])],
    )
    result = FileSystemSourceDiscovery().discover(tmp_path)
    assert [module.path for module in result.modules] == ["missing.py"]
    assert [(item.code, item.path, item.severity) for item in result.diagnostics] == [
        ("source_read_error", "missing.py", Severity.ERROR)
    ]


def test_inventory_recognizes_stub_and_native_targets_without_graph_nodes(
    tmp_path: Path,
) -> None:
    _write_files(
        tmp_path,
        [
            "app.py",
            "pkg/stub.pyi",
            "pkg/native.pyx",
            "pkg/compiled.cpython-313-x86_64-linux-gnu.so",
            "pkg/win.cp313-win_amd64.pyd",
            "pkg/helper.c",
            "pkg/binary.unknown.so",
            "pkg/stubs/__init__.pyi",
        ],
    )
    result = FileSystemSourceDiscovery().discover(tmp_path)
    assert [module.path for module in result.modules] == ["app.py"]
    assert {(target.name, target.kind) for target in result.targets} == {
        ("pkg.stub", "stub"),
        ("pkg.native", "native"),
        ("pkg.compiled", "native"),
        ("pkg.win", "native"),
        ("pkg.stubs", "stub"),
    }
    assert all(not target.acknowledged for target in result.targets)


def test_selected_root_pruning_is_exact_and_does_not_create_excluded_target(
    tmp_path: Path,
) -> None:
    _write_files(tmp_path, ["main.py", "src/pkg/a.py", "nested/src/kept.py"])
    result = FileSystemSourceDiscovery().discover(tmp_path, pruned_directories=("src",))
    assert [module.path for module in result.modules] == [
        "main.py",
        "nested/src/kept.py",
    ]
    assert result.excluded_paths == (ExcludedPath("src", "selected-root", "directory"),)
    assert result.targets == ()
