"""Assemble one immutable catalog from independently collected import roots."""

from collections import defaultdict
from dataclasses import replace
from pathlib import Path

from pyarchgraph.application.ports import (
    ImportFactSource,
    ProjectAccess,
    SourceDiscovery,
)
from pyarchgraph.domain.canonicalization import FactCanonicalizer
from pyarchgraph.domain.catalog import SourceCatalog
from pyarchgraph.domain.location import ProjectLocation
from pyarchgraph.domain.model import (
    AnalysisOptions,
    Diagnostic,
    ExcludedPath,
    ImportFact,
    Severity,
    SourceModule,
    TargetDeclaration,
)

TEST_EXCLUDES = ("tests", "test_*.py", "*_test.py")


class BindingReconciler:
    def reconcile(
        self,
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


class SourceCatalogBuilder:
    def __init__(
        self,
        discovery: SourceDiscovery,
        fact_source: ImportFactSource,
        project_access: ProjectAccess,
        canonicalizer: FactCanonicalizer,
        bindings: BindingReconciler,
    ) -> None:
        self.discovery = discovery
        self.fact_source = fact_source
        self.project_access = project_access
        self.canonicalizer = canonicalizer
        self.bindings = bindings

    @staticmethod
    def _locate(diagnostic: Diagnostic, prefix: Path) -> Diagnostic:
        return replace(
            diagnostic,
            path=(prefix / diagnostic.path).as_posix()
            if diagnostic.path
            else prefix.as_posix(),
            column=diagnostic.column + 1 if diagnostic.column is not None else None,
        )

    def build(
        self, location: ProjectLocation, options: AnalysisOptions
    ) -> SourceCatalog:
        roots = location.roots
        effective_excludes = tuple(sorted(set(options.excludes) | set(TEST_EXCLUDES)))
        modules: list[SourceModule] = []
        facts: list[ImportFact] = []
        diagnostics: list[Diagnostic] = []
        targets: list[TargetDeclaration] = []
        excluded_paths: list[ExcludedPath] = []
        namespaces = set()
        for root in roots:
            prefix = Path(self.project_access.relative(root, location.base_dir))
            pruned = tuple(
                other.relative_to(root).as_posix()
                for other in roots
                if other != root and other.is_relative_to(root)
            )
            discovery = self.discovery.discover(
                root, excludes=effective_excludes, pruned_directories=pruned
            )

            diagnostics.extend(
                self._locate(item, prefix) for item in discovery.diagnostics
            )
            collection = self.fact_source.collect(root, discovery.modules)
            diagnostics.extend(
                self._locate(item, prefix) for item in collection.diagnostics
            )
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
                    analysis_status="error"
                    if module.path in failed_paths
                    else "analyzed",
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
        modules, conflicts = self.bindings.reconcile(
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
        facts = self.canonicalizer.canonicalise(facts)
        return SourceCatalog(
            roots=roots,
            modules=modules,
            facts=facts,
            diagnostics=tuple(diagnostics),
            targets=tuple(targets),
            excluded_paths=tuple(excluded_paths),
            namespace_prefixes=tuple(sorted(namespaces)),
            effective_excludes=effective_excludes,
        )
