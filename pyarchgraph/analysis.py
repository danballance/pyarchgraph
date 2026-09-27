"""Analyze explicitly selected import roots, retaining useful partial results."""

import configparser
import os
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import tomllib

from pyarchgraph.configuration import validate_options
from pyarchgraph.discovery import discover_modules
from pyarchgraph.extraction import AstImportFactSource, canonicalise_fact_ids
from pyarchgraph.findings import build_views
from pyarchgraph.model import (
    AnalysisOptions,
    AnalysisReport,
    Coverage,
    Diagnostic,
    ExcludedPath,
    ExternalImport,
    ImportFact,
    Severity,
    SourceModule,
    TargetBoundary,
    TargetDeclaration,
)
from pyarchgraph.resolution import architecture_dependencies, resolve_imports

TEST_EXCLUDES = ("tests", "test_*.py", "*_test.py")
_DEFAULT_OPTIONS = AnalysisOptions()


class AnalysisError(ValueError):
    """Invalid analysis configuration; source failures instead return a report."""


def _relative(path: Path) -> str:
    return Path(os.path.relpath(path, Path.cwd())).as_posix()


def _metadata_layout(root: Path) -> tuple[set[str], dict[str, str]]:
    """Read literal setuptools roots and package aliases without build execution."""
    candidates: set[str] = set()
    package_dirs: dict[str, str] = {}
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            with pyproject.open("rb") as stream:
                data = tomllib.load(stream)
            settings = data.get("tool", {}).get("setuptools", {})
            package_dir = settings.get("package-dir", {})
            if isinstance(package_dir, dict):
                package_dirs.update(
                    (name, value)
                    for name, value in package_dir.items()
                    if isinstance(name, str) and isinstance(value, str)
                )
            packages = settings.get("packages", {})
            if isinstance(packages, dict):
                where = packages.get("find", {}).get("where", [])
                if isinstance(where, list):
                    candidates.update(
                        value for value in where if isinstance(value, str)
                    )
        except (OSError, ValueError, AttributeError, TypeError):
            pass
    setup_cfg = root / "setup.cfg"
    if setup_cfg.is_file():
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(setup_cfg, encoding="utf-8")
            if parser.has_option("options", "package_dir"):
                for line in parser.get("options", "package_dir").splitlines():
                    if "=" in line:
                        name, _, value = line.partition("=")
                        package_dirs[name.strip()] = value.strip()
            if parser.has_option("options.packages.find", "where"):
                candidates.update(
                    parser.get("options.packages.find", "where").splitlines()
                )
        except (OSError, ValueError, configparser.Error):
            pass

    def relative_directory(value: str) -> bool:
        return (
            bool(value)
            and not Path(value).is_absolute()
            and ".." not in Path(value).parts
        )

    aliases = {}
    for name, value in package_dirs.items():
        if not relative_directory(value):
            continue
        path = Path(value)
        if not name:
            candidates.add(value)
            continue
        package_parts = tuple(name.split("."))
        if path.parts[-len(package_parts) :] == package_parts:
            candidates.add(Path(*path.parts[: -len(package_parts)]).as_posix())
        else:
            # Renaming a directory as a package cannot be represented merely
            # by selecting another import root. Keep the alias for diagnostics.
            aliases[name] = path.as_posix()
    return {
        Path(value.strip()).as_posix().strip("/")
        for value in candidates
        if relative_directory(value.strip()) and Path(value.strip()).as_posix() != "."
    }, aliases


def _root_diagnostics(
    roots: tuple[Path, ...],
    modules: tuple[SourceModule, ...],
    external_imports: tuple[ExternalImport, ...],
) -> tuple[Diagnostic, ...]:
    """Require corroborating imports before diagnosing a root mismatch."""
    diagnostics = []
    requested_names = {item.requested for item in external_imports}
    for root in roots:
        root_prefix = _relative(root)
        members = []
        for module in modules:
            absolute = Path(os.path.abspath(module.path))
            if absolute.is_relative_to(root):
                members.append(module)
        hints, aliases = _metadata_layout(root)
        known_names = set()
        for module in members:
            if module.import_name:
                parts = module.import_name.split(".")
                known_names.update(
                    ".".join(parts[:index]) for index in range(1, len(parts) + 1)
                )
        for package, directory in sorted(aliases.items()):
            requested = sorted(
                name
                for name in requested_names
                if name == package or name.startswith(package + ".")
            )
            directory_path = root / directory
            if requested and any(
                Path(os.path.abspath(module.path)).is_relative_to(directory_path)
                for module in members
            ):
                diagnostics.append(
                    Diagnostic(
                        Severity.ERROR,
                        "unsupported_package_mapping",
                        f"Packaging maps {package!r} to {directory!r}, matching imports "
                        f"{', '.join(requested)}. This renamed package cannot be represented "
                        "by the selected import roots; use a source layout with matching package paths.",
                        path=root_prefix,
                    )
                )
        for candidate in sorted(hints | {"src", "lib"}):
            candidate_root = (root / candidate).resolve()
            if candidate_root in roots or not candidate_root.is_dir():
                continue
            name_prefix = candidate.replace("/", ".") + "."
            matches = sorted(
                {name for name in requested_names if name_prefix + name in known_names}
            )
            if matches:
                diagnostics.append(
                    Diagnostic(
                        Severity.ERROR,
                        "source_root_mismatch",
                        f"Imports {', '.join(matches)} match source below {candidate}/; "
                        f"select {_relative(candidate_root)!r} as an import root "
                        "(alongside the entrypoint root when needed).",
                        path=root_prefix,
                    )
                )
        # Nonstandard layouts without packaging evidence are suggestions only.
        possible = set()
        for module in members:
            if not module.import_name:
                continue
            parts = module.import_name.split(".")
            for index in range(1, len(parts)):
                suffix = ".".join(parts[index:])
                prefix = "/".join(parts[:index])
                if suffix in requested_names and prefix not in hints | {"src", "lib"}:
                    possible.add(prefix)
        for candidate in sorted(possible):
            if (root / candidate).resolve() not in roots:
                diagnostics.append(
                    Diagnostic(
                        Severity.WARNING,
                        "possible_source_root",
                        f"An external import resembles source below {candidate}/. "
                        "Check whether this directory is an import root or vendored source.",
                        path=root_prefix,
                    )
                )
    return tuple(diagnostics)


def _merge_bindings(
    modules: tuple[SourceModule, ...],
) -> tuple[tuple[SourceModule, ...], tuple[Diagnostic, ...]]:
    by_name: dict[str, list[SourceModule]] = defaultdict(list)
    for module in modules:
        if module.import_name and module.binding_status == "bound":
            by_name[module.import_name].append(module)
    ambiguous = {name for name, items in by_name.items() if len(items) > 1}
    # A module selected in one root cannot be an ordinary package prefix in another.
    nonpackages = {
        name
        for name, items in by_name.items()
        if any(not item.is_package for item in items)
    }
    for name in by_name:
        parts = name.split(".")
        for index in range(1, len(parts)):
            prefix = ".".join(parts[:index])
            if prefix in nonpackages:
                ambiguous.update((prefix, name))
            for package in by_name.get(prefix, ()):
                if package.is_package and any(
                    not Path(item.path).is_relative_to(Path(package.path).parent)
                    for item in by_name[name]
                ):
                    # Regular packages do not merge another root's namespace
                    # fragments into their __path__. Keep safe parent bindings.
                    ambiguous.add(name)
    diagnostics = tuple(
        Diagnostic(
            Severity.ERROR,
            "ambiguous_import_binding",
            f"Import name {name!r} has incompatible bindings across selected roots: "
            + ", ".join(item.path for item in by_name[name]),
            path=by_name[name][0].path,
        )
        for name in sorted(ambiguous)
    )
    return tuple(
        replace(module, binding_status="ambiguous")
        if module.import_name in ambiguous and module.binding_status == "bound"
        else module
        for module in modules
    ), diagnostics


def _target_layout_diagnostics(
    modules: tuple[SourceModule, ...],
    boundaries: tuple[TargetBoundary, ...],
    discovered_targets: list[TargetDeclaration],
) -> tuple[Diagnostic, ...]:
    """Do not accept another root's omitted/native files as package children."""
    discovered = {(target.kind, target.path) for target in discovered_targets}
    bindings = {
        module.import_name: module
        for module in modules
        if module.import_name and module.binding_status == "bound"
    }
    diagnostics = []
    for boundary in boundaries:
        parts = boundary.name.split(".")
        for index in range(1, len(parts)):
            package = bindings.get(".".join(parts[:index]))
            if package and not package.is_package:
                diagnostics.append(
                    Diagnostic(
                        Severity.ERROR,
                        "non_package_target_prefix",
                        f"Target {boundary.name!r} is below non-package source "
                        f"{package.import_name!r}; accepting a boundary cannot make that source a package.",
                        path=boundary.path or package.path,
                    )
                )
                break
            if (
                package
                and boundary.path
                and (boundary.kind, boundary.path) in discovered
                and not Path(boundary.path).is_relative_to(Path(package.path).parent)
            ):
                diagnostics.append(
                    Diagnostic(
                        Severity.ERROR,
                        "incompatible_target_layout",
                        f"Target {boundary.name!r} is outside the selected regular package "
                        f"{package.import_name!r}; separate roots cannot merge this package's contents.",
                        path=boundary.path,
                    )
                )
                break
    return tuple(diagnostics)


def analyse(
    source_roots: tuple[Path, ...],
    *,
    options: AnalysisOptions = _DEFAULT_OPTIONS,
) -> AnalysisReport:
    """Parse explicit imports without executing source or changing the selected roots."""
    validate_options(options)
    if not isinstance(source_roots, tuple) or not source_roots:
        raise AnalysisError("source_roots must be a nonempty tuple of directories")
    roots = tuple(sorted({Path(root).resolve() for root in source_roots}, key=str))
    if any(not root.is_dir() for root in roots):
        raise AnalysisError("every source root must be an existing directory")
    effective_excludes = tuple(sorted(set(options.excludes) | set(TEST_EXCLUDES)))
    modules: list[SourceModule] = []
    facts: list[ImportFact] = []
    diagnostics: list[Diagnostic] = []
    targets: list[TargetDeclaration] = []
    excluded_paths: list[ExcludedPath] = []
    namespaces = set()
    for root in roots:
        prefix = Path(_relative(root))
        pruned = tuple(
            other.relative_to(root).as_posix()
            for other in roots
            if other != root and other.is_relative_to(root)
        )
        discovery = discover_modules(
            root, excludes=effective_excludes, pruned_directories=pruned
        )

        def locate(diagnostic: Diagnostic, prefix: Path = prefix) -> Diagnostic:
            return replace(
                diagnostic,
                path=(prefix / diagnostic.path).as_posix()
                if diagnostic.path
                else prefix.as_posix(),
                column=diagnostic.column + 1 if diagnostic.column is not None else None,
            )

        diagnostics.extend(locate(item) for item in discovery.diagnostics)
        collection = AstImportFactSource().collect(root, discovery.modules)
        diagnostics.extend(locate(item) for item in collection.diagnostics)
        failed_paths = {
            item.path
            for item in collection.diagnostics
            if item.severity is Severity.ERROR
        }
        id_map = {
            module.id: "source:" + (prefix / module.path).as_posix()
            for module in discovery.modules
        }
        modules.extend(
            replace(
                module,
                id=id_map[module.id],
                path=(prefix / module.path).as_posix(),
                analysis_status="error" if module.path in failed_paths else "analyzed",
            )
            for module in discovery.modules
        )
        facts.extend(
            replace(
                fact,
                id="",
                source=id_map[fact.source],
                path=(prefix / fact.path).as_posix(),
            )
            for fact in collection.facts
        )
        targets.extend(
            replace(
                target,
                path=(
                    (prefix / target.path).as_posix()
                    + ("/" if target.path.endswith("/") else "")
                )
                if target.path
                else None,
            )
            for target in discovery.targets
        )
        excluded_paths.extend(
            replace(item, path=(prefix / item.path).as_posix())
            for item in discovery.excluded_paths
        )
        namespaces.update(discovery.namespace_prefixes)
    modules, conflicts = _merge_bindings(
        tuple(sorted(modules, key=lambda item: item.id))
    )
    diagnostics.extend(conflicts)
    if not modules:
        diagnostics.append(
            Diagnostic(
                Severity.ERROR,
                "no_sources",
                "Selected roots contain no Python sources.",
            )
        )
    facts = canonicalise_fact_ids(facts)
    # Configuration may acknowledge discovered targets, but cannot create a source binding.
    configured = {item.name: item for item in options.targets}
    seen_targets = set()
    merged_targets = []
    for target in targets:
        override = configured.get(target.name)
        if override is not None:
            if override.kind == target.kind:
                target = replace(
                    target,
                    acknowledged=override.acknowledged,
                    reason=override.reason,
                    path=target.path or override.path,
                )
                seen_targets.add(target.name)
            elif target.kind != "stub" or override.kind not in ("native", "generated"):
                raise AnalysisError(
                    f"target {target.name!r} kind disagrees with discovered {target.kind} source"
                )
        merged_targets.append(target)
    merged_targets.extend(
        item for item in options.targets if item.name not in seen_targets
    )
    resolution = resolve_imports(
        facts,
        modules,
        tuple(sorted(namespaces)),
        owned_prefixes=options.owned_prefixes,
        targets=tuple(merged_targets),
    )
    diagnostics.extend(resolution.diagnostics)
    diagnostics.extend(
        _target_layout_diagnostics(modules, resolution.boundaries, targets)
    )
    diagnostics.extend(_root_diagnostics(roots, modules, resolution.external_imports))
    used_boundaries = {item.name for item in resolution.boundaries}
    for target in options.targets:
        if target.acknowledged and target.name not in used_boundaries:
            diagnostics.append(
                Diagnostic(
                    Severity.WARNING,
                    "unused_acknowledgement",
                    f"Acknowledgement for {target.name!r} did not apply to an unanalysed target.",
                    path=target.path,
                )
            )
    structural = architecture_dependencies(resolution.dependencies)
    views = build_views(
        structural, facts, resolution.unresolved_imports, details=options.details
    )
    diagnostics = tuple(
        sorted(
            set(diagnostics),
            key=lambda item: (
                item.severity.value,
                item.code,
                item.path or "",
                item.line or 0,
                item.column or 0,
                item.message,
            ),
        )
    )
    incomplete = any(item.severity is Severity.ERROR for item in diagnostics) or any(
        not boundary.acknowledged for boundary in resolution.boundaries
    )
    return AnalysisReport(
        status="incomplete" if incomplete else "complete",
        gate=options.gate,
        sources=modules,
        coverage=Coverage(
            roots=tuple(_relative(root) for root in roots),
            excludes=effective_excludes,
            excluded_paths=tuple(
                sorted(
                    set(excluded_paths),
                    key=lambda item: (item.path, item.rule, item.kind),
                )
            ),
            analyzed_source_count=sum(
                module.analysis_status == "analyzed" for module in modules
            ),
            diagnostics=diagnostics,
            boundaries=resolution.boundaries,
        ),
        views=views,
    )
