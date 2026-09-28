"""Inventory source files and import bindings without executing project code."""

from __future__ import annotations

import importlib.machinery
import os
import re
import stat
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path, PurePosixPath

from pyarchgraph.application.ports.sources import (
    DiscoveryResult,
    ExcludedPath,
    SourceDiscovery,
)
from pyarchgraph.domain.models import (
    Diagnostic,
    Severity,
    SourceModule,
    TargetDeclaration,
)

DEFAULT_EXCLUDED_DIRECTORY_BASENAMES = frozenset(
    {".git", ".venv", "venv", "__pycache__", "build", "dist"}
)


class FileSystemSourceDiscovery(SourceDiscovery):
    """Inventory source modules, import bindings and target declarations on disk.

    Project code is never imported or executed.
    """

    def discover(
        self,
        source_root: Path,
        *,
        excludes: tuple[str, ...] = (),
        pruned_directories: tuple[str, ...] = (),
    ) -> DiscoveryResult:
        """Discover all ``.py`` files, including sources without dotted identities.

        Exclusions use POSIX-relative ``PurePosixPath.match`` rules. Directory
        symlinks and excluded directories are recorded without traversing them.
        File symlinks remain separate logical source paths. Stub/native files are
        availability evidence, never additional Python graph nodes.
        """
        root = Path(source_root)
        patterns = tuple(sorted(set(excludes)))
        self._validate_excludes(patterns)
        paths, diagnostics, targets, excluded = self._python_paths(
            root, patterns, frozenset(pruned_directories)
        )
        candidates = []
        for path in paths:
            module, diagnostic = self._candidate_from_path(path)
            candidates.append(module)
            if diagnostic:
                diagnostics.append(diagnostic)
        modules, conflicts = self._remove_ambiguous_groups(candidates)
        diagnostics.extend(conflicts)
        bound_names = {
            module.import_name
            for module in modules
            if module.import_name and module.binding_status == "bound"
        }
        namespace_prefixes = tuple(
            sorted(
                {
                    prefix
                    for name in bound_names | {target.name for target in targets}
                    for prefix in self._proper_prefixes(name)
                    if prefix not in bound_names
                }
            )
        )
        return DiscoveryResult(
            modules=modules,
            namespace_prefixes=namespace_prefixes,
            diagnostics=tuple(sorted(diagnostics, key=self._diagnostic_sort_key)),
            targets=tuple(
                sorted(
                    set(targets),
                    key=lambda item: (item.name, item.kind, item.path or ""),
                )
            ),
            excluded_paths=tuple(
                sorted(excluded, key=lambda item: (item.path, item.rule, item.kind))
            ),
        )

    def _validate_excludes(self, patterns: tuple[str, ...]) -> None:
        for pattern in patterns:
            if not pattern:
                raise ValueError("exclude patterns must not be empty")
            if PurePosixPath(pattern).is_absolute():
                raise ValueError("exclude patterns must be POSIX-relative")
            PurePosixPath("validation-path").match(pattern)

    def _lossless_name(self, parts: tuple[str, ...]) -> str | None:
        return (
            ".".join(parts)
            if parts and all((part and "." not in part for part in parts))
            else None
        )

    def _artifact_identity(self, path: PurePosixPath) -> tuple[str | None, str | None]:
        filename = path.name
        if filename.endswith((".py", ".pyi", ".pyx")):
            suffix = path.suffix
            stem = filename[: -len(suffix)]
            kind = {".py": None, ".pyi": "stub", ".pyx": "native"}[suffix]
        else:
            suffixes = set(importlib.machinery.EXTENSION_SUFFIXES) | {".so", ".pyd"}
            suffix = next(
                (
                    suffix
                    for suffix in sorted(suffixes, key=lambda item: (-len(item), item))
                    if filename.endswith(suffix)
                ),
                None,
            )
            if suffix is None:
                return (None, None)
            stem = filename[: -len(suffix)]
            if re.search("\\.(?:cpython-\\d+[^.]*|cp\\d+[^.]*|abi3)$", stem):
                stem = stem.split(".", 1)[0]
            kind = "native"
        parts = path.parts[:-1] if stem == "__init__" else (*path.parts[:-1], stem)
        return (self._lossless_name(parts), kind)

    def _python_paths(
        self,
        source_root: Path,
        exclude_patterns: tuple[str, ...],
        pruned_directories: frozenset[str],
    ) -> tuple[
        list[PurePosixPath],
        list[Diagnostic],
        list[TargetDeclaration],
        list[ExcludedPath],
    ]:
        return _DiscoverySession(
            self, source_root, exclude_patterns, pruned_directories
        ).scan()

    def _relative_error_path(
        self, source_root: Path, filename: str | bytes | None
    ) -> str | None:
        if filename is None:
            return None
        try:
            return Path(os.fsdecode(filename)).relative_to(source_root).as_posix()
        except (TypeError, ValueError):
            return None

    def _exclusion_rule(
        self, path: PurePosixPath, patterns: tuple[str, ...]
    ) -> str | None:
        return next((pattern for pattern in patterns if path.match(pattern)), None)

    def _candidate_from_path(
        self, relative_path: PurePosixPath
    ) -> tuple[SourceModule, Diagnostic | None]:
        path = relative_path.as_posix()
        is_package = relative_path.name == "__init__.py"
        import_name, _ = self._artifact_identity(relative_path)
        diagnostic = None
        if relative_path == PurePosixPath("__init__.py"):
            diagnostic = Diagnostic(
                Severity.ERROR,
                "root_init_unsupported",
                "A source-root-level __init__.py has no package identity; pass its parent directory as the import root.",
                path,
            )
        elif import_name is None:
            diagnostic = Diagnostic(
                Severity.INFO,
                "path_only_source",
                "Source is analyzed by path because its filename cannot map losslessly to a dotted import name.",
                path,
            )
        return (
            SourceModule(
                id=f"source:{path}",
                path=path,
                is_package=is_package,
                parent_package=import_name.rpartition(".")[0] or None
                if import_name
                else None,
                import_name=import_name,
                binding_status="bound" if import_name else "path_only",
            ),
            diagnostic,
        )

    def _remove_ambiguous_groups(
        self, candidates: Iterable[SourceModule]
    ) -> tuple[tuple[SourceModule, ...], tuple[Diagnostic, ...]]:
        """Keep all source files while selecting normal same-root import bindings."""
        ordered = sorted(candidates, key=lambda item: (item.id, item.path))
        groups: defaultdict[str, list[int]] = defaultdict(list)
        for index, module in enumerate(ordered):
            if module.import_name:
                groups[module.import_name].append(index)
        diagnostics = []
        for name, indexes in sorted(groups.items()):
            if len(indexes) < 2:
                continue
            packages = [index for index in indexes if ordered[index].is_package]
            if len(packages) == 1:
                for index in indexes:
                    if index != packages[0]:
                        ordered[index] = replace(
                            ordered[index], binding_status="shadowed"
                        )
                        diagnostics.append(
                            Diagnostic(
                                Severity.INFO,
                                "shadowed_source",
                                f"The regular package takes precedence for import {name!r}; this source's outgoing imports are still analyzed.",
                                ordered[index].path,
                            )
                        )
            else:
                for index in indexes:
                    ordered[index] = replace(ordered[index], binding_status="ambiguous")
                diagnostics.append(
                    Diagnostic(
                        Severity.ERROR,
                        "duplicate_module_id",
                        f"Import name {name!r} is produced by multiple source files.",
                        ordered[indexes[0]].path,
                    )
                )
        non_packages = {
            module.import_name
            for module in ordered
            if module.import_name
            and module.binding_status == "bound"
            and (not module.is_package)
        }
        for index, module in enumerate(ordered):
            if module.import_name and any(
                (
                    prefix in non_packages
                    for prefix in self._proper_prefixes(module.import_name)
                )
            ):
                ordered[index] = replace(
                    module, binding_status="path_only", parent_package=None
                )
                diagnostics.append(
                    Diagnostic(
                        Severity.INFO,
                        "non_package_prefix_conflict",
                        "A non-package source blocks this dotted import name; outgoing absolute imports are still analyzed.",
                        module.path,
                    )
                )
        return (
            tuple(ordered),
            tuple(sorted(diagnostics, key=self._diagnostic_sort_key)),
        )

    def _proper_prefixes(self, module_id: str) -> tuple[str, ...]:
        parts = module_id.split(".")
        return tuple((".".join(parts[:index]) for index in range(1, len(parts))))

    def _diagnostic_sort_key(self, diagnostic: Diagnostic) -> tuple[object, ...]:
        return (
            diagnostic.severity.value,
            diagnostic.code,
            diagnostic.path or "",
            diagnostic.line or -1,
            diagnostic.column if diagnostic.column is not None else -1,
            diagnostic.message,
        )


class _DiscoverySession:
    """Gather files, target declarations and diagnostics for one filesystem scan."""

    def __init__(
        self,
        discovery: FileSystemSourceDiscovery,
        source_root: Path,
        exclude_patterns: tuple[str, ...],
        pruned_directories: frozenset[str],
    ) -> None:
        self.discovery = discovery
        self.source_root = source_root
        self.exclude_patterns = exclude_patterns
        self.pruned_directories = pruned_directories
        self.discovered: list[PurePosixPath] = []
        self.diagnostics: list[Diagnostic] = []
        self.targets: list[TargetDeclaration] = []
        self.excluded: list[ExcludedPath] = []

    def scan(
        self,
    ) -> tuple[
        list[PurePosixPath],
        list[Diagnostic],
        list[TargetDeclaration],
        list[ExcludedPath],
    ]:
        for directory, directory_names, file_names in os.walk(
            self.source_root,
            topdown=True,
            onerror=self.record_walk_error,
            followlinks=False,
        ):
            directory_path = Path(directory)
            try:
                relative_directory = directory_path.relative_to(self.source_root)
            except ValueError:
                continue
            kept = []
            for name in sorted(directory_names):
                path = PurePosixPath(relative_directory.as_posix(), name)
                if path.as_posix() in self.pruned_directories:
                    self.omit(path, "selected-root", directory=True)
                elif name in DEFAULT_EXCLUDED_DIRECTORY_BASENAMES:
                    self.omit(path, f"default:{name}", directory=True)
                elif (directory_path / name).is_symlink():
                    self.omit(path, "directory_symlink", directory=True)
                elif rule := self.discovery._exclusion_rule(
                    path, self.exclude_patterns
                ):
                    self.omit(path, rule, directory=True)
                else:
                    kept.append(name)
            directory_names[:] = kept
            for name in sorted(file_names):
                path = PurePosixPath(relative_directory.as_posix(), name)
                artifact_name, kind = self.discovery._artifact_identity(path)
                is_source = name.endswith(".py")
                if not is_source and kind is None:
                    continue
                if rule := self.discovery._exclusion_rule(path, self.exclude_patterns):
                    self.omit(path, rule, directory=False)
                    continue
                if is_source:
                    self.discovered.append(path)
                try:
                    regular = stat.S_ISREG((directory_path / name).stat().st_mode)
                except OSError:
                    self.diagnostics.append(
                        Diagnostic(
                            Severity.ERROR,
                            "source_read_error",
                            "Could not inspect a source input.",
                            path.as_posix(),
                        )
                    )
                    continue
                if not regular:
                    self.diagnostics.append(
                        Diagnostic(
                            Severity.ERROR,
                            "source_not_regular",
                            "Source input must be a regular file.",
                            path.as_posix(),
                        )
                    )
                    continue
                if artifact_name and kind:
                    self.targets.append(
                        TargetDeclaration(
                            name=artifact_name,
                            kind=kind,
                            path=path.as_posix(),
                            reason="Typing stub exists; its implementation is outside the Python source graph."
                            if kind == "stub"
                            else "Native source or extension exists; its implementation is outside the Python source graph.",
                        )
                    )
        return (
            sorted(set(self.discovered), key=str),
            self.diagnostics,
            self.targets,
            self.excluded,
        )

    def record_walk_error(self, error: OSError) -> None:
        self.diagnostics.append(
            Diagnostic(
                severity=Severity.ERROR,
                code="source_read_error",
                message="Could not scan a source directory.",
                path=self.discovery._relative_error_path(
                    self.source_root, error.filename
                ),
            )
        )

    def omit(self, path: PurePosixPath, rule: str, *, directory: bool) -> None:
        self.excluded.append(
            ExcludedPath(path.as_posix(), rule, "directory" if directory else "file")
        )
        name = (
            self.discovery._lossless_name(path.parts)
            if directory
            else self.discovery._artifact_identity(path)[0]
        )
        if name and rule != "selected-root":
            self.targets.append(
                TargetDeclaration(
                    name=name,
                    kind="excluded",
                    path=path.as_posix() + ("/" if directory else ""),
                    reason=f"Source {('directory' if directory else 'file')} excluded by {rule!r}.",
                    acknowledged=rule != "directory_symlink",
                )
            )
