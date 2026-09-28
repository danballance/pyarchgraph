"""Reconcile target declarations and enforce Python package boundaries."""

from dataclasses import replace
from pathlib import PurePosixPath

from pyarchgraph.domain.models import (
    Diagnostic,
    Severity,
    SourceModule,
    TargetBoundary,
    TargetDeclaration,
)


class TargetReconciler:
    """Combine target declarations and check they fit the source package layout."""

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
                    raise ValueError(
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
                    and not PurePosixPath(boundary.path).is_relative_to(
                        PurePosixPath(package.path).parent
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
