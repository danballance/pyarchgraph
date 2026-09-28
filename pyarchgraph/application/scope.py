"""Diagnose source-root layout using project metadata observations."""

from pyarchgraph.application.ports.project import ProjectAccess, ProjectLocation
from pyarchgraph.domain.models import Diagnostic, ExternalImport, Severity, SourceModule


class LayoutDiagnosticService:
    """Reports evidence that the selected import roots do not match the project layout."""

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
