"""Independent target reconciliation, layout diagnostics and coverage services."""

from dataclasses import replace
from pathlib import Path

from pyarchgraph.application.ports import ProjectAccess
from pyarchgraph.domain.errors import AnalysisError
from pyarchgraph.domain.location import ProjectLocation
from pyarchgraph.domain.model import (
    Diagnostic,
    ExternalImport,
    Severity,
    SourceModule,
    TargetBoundary,
    TargetDeclaration,
)


class TargetReconciler:
    def reconcile(
        self,
        targets: tuple[TargetDeclaration, ...],
        configured_targets: tuple[TargetDeclaration, ...],
    ) -> tuple[TargetDeclaration, ...]:
        # Configuration may acknowledge discovered targets, but cannot create a source binding.
        configured = {item.name: item for item in configured_targets}
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
                elif target.kind != "stub" or override.kind not in (
                    "native",
                    "generated",
                ):
                    raise AnalysisError(
                        f"target {target.name!r} kind disagrees with discovered {target.kind} source"
                    )
            merged_targets.append(target)
        merged_targets.extend(
            item for item in configured_targets if item.name not in seen_targets
        )
        return tuple(merged_targets)

    def diagnose_layout(
        self,
        modules: tuple[SourceModule, ...],
        boundaries: tuple[TargetBoundary, ...],
        discovered_targets: tuple[TargetDeclaration, ...],
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
                    and not Path(boundary.path).is_relative_to(
                        Path(package.path).parent
                    )
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


class LayoutDiagnosticService:
    def __init__(self, project_access: ProjectAccess) -> None:
        self.project_access = project_access

    def diagnose(
        self,
        location: ProjectLocation,
        modules: tuple[SourceModule, ...],
        external_imports: tuple[ExternalImport, ...],
    ) -> tuple[Diagnostic, ...]:
        """Require corroborating imports before diagnosing a root mismatch."""
        roots = location.roots
        diagnostics = []
        requested_names = {item.requested for item in external_imports}
        for root in roots:
            root_prefix = self.project_access.relative(root, location.base_dir)
            members = []
            for module in modules:
                absolute = self.project_access.absolute(module.path, location.base_dir)
                if absolute.is_relative_to(root):
                    members.append(module)
            metadata = self.project_access.read_metadata(root)
            hints = set(metadata.root_candidates)
            aliases = dict(metadata.package_aliases)
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
                    self.project_access.absolute(
                        module.path, location.base_dir
                    ).is_relative_to(directory_path)
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
                directory = self.project_access.directory(root, candidate)
                candidate_root = directory.path
                if candidate_root in roots or not directory.exists:
                    continue
                name_prefix = candidate.replace("/", ".") + "."
                matches = sorted(
                    {
                        name
                        for name in requested_names
                        if name_prefix + name in known_names
                    }
                )
                if matches:
                    diagnostics.append(
                        Diagnostic(
                            Severity.ERROR,
                            "source_root_mismatch",
                            f"Imports {', '.join(matches)} match source below {candidate}/; "
                            f"select {self.project_access.relative(candidate_root, location.base_dir)!r} as an import root "
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
                    if suffix in requested_names and prefix not in hints | {
                        "src",
                        "lib",
                    }:
                        possible.add(prefix)
            for candidate in sorted(possible):
                if self.project_access.directory(root, candidate).path not in roots:
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
