"""Assemble one immutable catalog from independently collected import roots."""

from dataclasses import dataclass, replace
from pathlib import Path

from pyarchgraph.application.ports.project import ProjectAccess, ProjectLocation
from pyarchgraph.application.ports.sources import (
    ExcludedPath,
    ImportFactSource,
    SourceDiscovery,
)
from pyarchgraph.application.requests import AnalysisOptions
from pyarchgraph.domain.bindings import BindingReconciler
from pyarchgraph.domain.canonicalization import FactCanonicalizer
from pyarchgraph.domain.models import (
    Diagnostic,
    ImportFact,
    ImportFactDraft,
    Severity,
    SourceModule,
    TargetDeclaration,
)

TEST_EXCLUDES = ("tests", "test_*.py", "*_test.py")


@dataclass(frozen=True, slots=True)
class SourceCatalog:
    """Brings together sources, import facts and coverage observations from all roots."""

    roots: tuple[Path, ...]
    modules: tuple[SourceModule, ...]
    facts: tuple[ImportFact, ...]
    diagnostics: tuple[Diagnostic, ...]
    targets: tuple[TargetDeclaration, ...] = ()
    excluded_paths: tuple[ExcludedPath, ...] = ()
    namespace_prefixes: tuple[str, ...] = ()
    effective_excludes: tuple[str, ...] = ()


class SourceCatalogBuilder:
    """Assembles one source catalogue and gives collected import drafts stable identities."""

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
        drafts: list[ImportFactDraft] = []
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
            drafts.extend(
                replace(
                    fact,
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
        facts = self.canonicalizer.canonicalise(drafts)
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
