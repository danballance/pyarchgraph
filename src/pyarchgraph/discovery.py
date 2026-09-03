"""Deterministic discovery of source-backed Python modules.

The supplied directory is an import root, rather than a package directory.
Discovery therefore maps paths exactly as they would be named from that root,
while allowing namespace-package-shaped directories.  It does not import or
otherwise execute any of the discovered source files.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import keyword
import os
from pathlib import Path, PurePosixPath
from typing import Iterable

from pyarchgraph.model import Diagnostic, Severity, SourceModule


DEFAULT_EXCLUDED_DIRECTORY_BASENAMES = frozenset(
    {".git", ".venv", "venv", "__pycache__", "build", "dist"}
)


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """The unambiguous module inventory and its discovery diagnostics."""

    modules: tuple[SourceModule, ...]
    namespace_prefixes: tuple[str, ...]
    diagnostics: tuple[Diagnostic, ...]


@dataclass(frozen=True, slots=True)
class _Candidate:
    module: SourceModule


def discover_modules(
    source_root: Path,
    *,
    excludes: tuple[str, ...] = (),
) -> DiscoveryResult:
    """Discover importable ``.py`` files beneath *source_root*.

    User exclusions are OR-combined POSIX-relative glob patterns.  They are
    evaluated against both directory and file paths with
    :meth:`PurePosixPath.match`.  Directory symlinks are never traversed.

    Invalid paths and ambiguous module groups are diagnosed and omitted.  The
    returned collections have canonical ordering independent of filesystem
    enumeration order.
    """

    source_root = Path(source_root)
    exclude_patterns = tuple(excludes)
    _validate_excludes(exclude_patterns)

    paths, scan_diagnostics = _python_paths(
        source_root, exclude_patterns=exclude_patterns
    )
    diagnostics = list(scan_diagnostics)
    candidates: list[_Candidate] = []

    for relative_path in paths:
        candidate, diagnostic = _candidate_from_path(relative_path)
        if diagnostic is not None:
            diagnostics.append(diagnostic)
        elif candidate is not None:
            candidates.append(candidate)

    retained, conflict_diagnostics = _remove_ambiguous_groups(candidates)
    diagnostics.extend(conflict_diagnostics)

    modules = tuple(
        sorted((candidate.module for candidate in retained), key=lambda item: item.id)
    )
    module_ids = {module.id for module in modules}
    namespace_prefixes = tuple(
        sorted(
            {
                prefix
                for module in modules
                for prefix in _proper_prefixes(module.id)
                if prefix not in module_ids
            }
        )
    )

    return DiscoveryResult(
        modules=modules,
        namespace_prefixes=namespace_prefixes,
        diagnostics=tuple(sorted(diagnostics, key=_diagnostic_sort_key)),
    )


def _validate_excludes(patterns: tuple[str, ...]) -> None:
    for pattern in patterns:
        if not pattern:
            raise ValueError("exclude patterns must not be empty")
        if PurePosixPath(pattern).is_absolute():
            raise ValueError("exclude patterns must be POSIX-relative")
        # Compile/validate Path.match's pattern before walking the tree.
        PurePosixPath("validation-path").match(pattern)


def _python_paths(
    source_root: Path,
    *,
    exclude_patterns: tuple[str, ...],
) -> tuple[tuple[PurePosixPath, ...], tuple[Diagnostic, ...]]:
    discovered: list[PurePosixPath] = []
    diagnostics: list[Diagnostic] = []

    def record_walk_error(error: OSError) -> None:
        path = _relative_error_path(source_root, error.filename)
        diagnostics.append(
            Diagnostic(
                severity=Severity.ERROR,
                code="source_read_error",
                message="Could not scan a source directory.",
                path=path,
            )
        )

    for directory, directory_names, file_names in os.walk(
        source_root,
        topdown=True,
        onerror=record_walk_error,
        followlinks=False,
    ):
        directory_path = Path(directory)
        try:
            relative_directory = directory_path.relative_to(source_root)
        except ValueError:
            # ``os.walk`` cannot ordinarily escape with ``followlinks=False``;
            # retaining this guard keeps canonical output free of absolute paths.
            continue

        kept_directories: list[str] = []
        for name in sorted(directory_names):
            relative_path = PurePosixPath(relative_directory.as_posix(), name)
            child_path = directory_path / name
            if name in DEFAULT_EXCLUDED_DIRECTORY_BASENAMES:
                continue
            if child_path.is_symlink():
                continue
            if _is_excluded(relative_path, exclude_patterns):
                continue
            kept_directories.append(name)
        directory_names[:] = kept_directories

        for name in sorted(file_names):
            if not name.endswith(".py"):
                continue
            relative_path = PurePosixPath(relative_directory.as_posix(), name)
            if _is_excluded(relative_path, exclude_patterns):
                continue
            discovered.append(relative_path)

    return tuple(sorted(set(discovered), key=str)), tuple(diagnostics)


def _relative_error_path(source_root: Path, filename: str | bytes | None) -> str | None:
    if filename is None:
        return None
    try:
        candidate = Path(os.fsdecode(filename))
        return candidate.relative_to(source_root).as_posix() or "."
    except (TypeError, ValueError):
        return None


def _is_excluded(path: PurePosixPath, patterns: tuple[str, ...]) -> bool:
    return any(path.match(pattern) for pattern in patterns)


def _candidate_from_path(
    relative_path: PurePosixPath,
) -> tuple[_Candidate | None, Diagnostic | None]:
    path = relative_path.as_posix()
    if relative_path == PurePosixPath("__init__.py"):
        return None, Diagnostic(
            severity=Severity.ERROR,
            code="root_init_unsupported",
            message=(
                "A source-root-level __init__.py is unsupported; pass its parent "
                "directory as the source root."
            ),
            path=path,
        )

    is_package = relative_path.name == "__init__.py"
    if is_package:
        module_parts = relative_path.parts[:-1]
    else:
        module_parts = (*relative_path.parts[:-1], relative_path.stem)

    if not module_parts or any(not _valid_module_part(part) for part in module_parts):
        return None, Diagnostic(
            severity=Severity.ERROR,
            code="invalid_module_id",
            message=(
                "Cannot derive a valid module ID; every path segment must be a "
                "non-keyword Python identifier."
            ),
            path=path,
        )

    module_id = ".".join(module_parts)
    parent_package = ".".join(module_parts[:-1]) if len(module_parts) > 1 else None
    return (
        _Candidate(
            SourceModule(
                id=module_id,
                path=path,
                is_package=is_package,
                parent_package=parent_package,
            )
        ),
        None,
    )


def _valid_module_part(part: str) -> bool:
    return part.isidentifier() and not keyword.iskeyword(part)


def _remove_ambiguous_groups(
    candidates: Iterable[_Candidate],
) -> tuple[tuple[_Candidate, ...], tuple[Diagnostic, ...]]:
    ordered = tuple(
        sorted(candidates, key=lambda item: (item.module.id, item.module.path))
    )
    if not ordered:
        return (), ()

    parents = list(range(len(ordered)))

    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    diagnostics: list[Diagnostic] = []
    conflicted: set[int] = set()
    by_id: defaultdict[str, list[int]] = defaultdict(list)
    for index, candidate in enumerate(ordered):
        by_id[candidate.module.id].append(index)

    for module_id, indexes in sorted(by_id.items()):
        if len(indexes) < 2:
            continue
        first = indexes[0]
        conflicted.update(indexes)
        for index in indexes[1:]:
            union(first, index)
        paths = tuple(ordered[index].module.path for index in indexes)
        diagnostics.append(
            Diagnostic(
                severity=Severity.ERROR,
                code="duplicate_module_id",
                message=(
                    f"Module ID {module_id!r} is produced by multiple source "
                    f"files: {', '.join(paths)}."
                ),
                path=paths[0],
            )
        )

    for prefix_id, prefix_indexes in sorted(by_id.items()):
        non_package_indexes = [
            index for index in prefix_indexes if not ordered[index].module.is_package
        ]
        if not non_package_indexes:
            continue
        descendant_indexes = [
            index
            for module_id, indexes in by_id.items()
            if module_id.startswith(prefix_id + ".")
            for index in indexes
        ]
        if not descendant_indexes:
            continue

        affected = (*non_package_indexes, *descendant_indexes)
        conflicted.update(affected)
        first = affected[0]
        for index in affected[1:]:
            union(first, index)
        descendant_paths = tuple(
            sorted(ordered[index].module.path for index in descendant_indexes)
        )
        prefix_paths = tuple(
            sorted(ordered[index].module.path for index in non_package_indexes)
        )
        diagnostics.append(
            Diagnostic(
                severity=Severity.ERROR,
                code="non_package_prefix_conflict",
                message=(
                    f"Non-package module {prefix_id!r} cannot prefix descendant "
                    f"modules from: {', '.join(descendant_paths)}."
                ),
                path=prefix_paths[0],
            )
        )

    # Any candidates connected to a conflict are part of the same ambiguous
    # identity group.  This matters when duplicate and prefix conflicts overlap.
    conflicted_roots = {find(index) for index in conflicted}
    excluded = {
        index for index in range(len(ordered)) if find(index) in conflicted_roots
    }
    retained = tuple(
        candidate for index, candidate in enumerate(ordered) if index not in excluded
    )
    return retained, tuple(diagnostics)


def _proper_prefixes(module_id: str) -> tuple[str, ...]:
    parts = module_id.split(".")
    return tuple(".".join(parts[:index]) for index in range(1, len(parts)))


def _diagnostic_sort_key(
    diagnostic: Diagnostic,
) -> tuple[str, str, str, int, int, str]:
    return (
        diagnostic.severity.value,
        diagnostic.code,
        diagnostic.path or "",
        diagnostic.line if diagnostic.line is not None else -1,
        diagnostic.column if diagnostic.column is not None else -1,
        diagnostic.message,
    )


__all__ = [
    "DEFAULT_EXCLUDED_DIRECTORY_BASENAMES",
    "DiscoveryResult",
    "discover_modules",
]
