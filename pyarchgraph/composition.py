"""The sole composition root for concrete runtime adapters."""

from collections.abc import Mapping

from pyarchgraph.adapters.cli import CliApplication
from pyarchgraph.adapters.configuration import TomlOptionsReader
from pyarchgraph.adapters.discovery import FileSystemSourceDiscovery
from pyarchgraph.adapters.extraction import (
    AstImportFactSource,
    ModuleImportExtractor,
    PythonAstParser,
    SourceReader,
)
from pyarchgraph.adapters.networkx_graph import NetworkXGraphAlgorithms
from pyarchgraph.adapters.project import FileSystemProjectAccess
from pyarchgraph.adapters.rendering import JsonReportRenderer
from pyarchgraph.application.analysis import AnalysisService
from pyarchgraph.application.catalog import BindingReconciler, SourceCatalogBuilder
from pyarchgraph.application.ports import (
    ImportFactSource,
    ProjectAccess,
    ProjectAnalyzer,
    SourceDiscovery,
)
from pyarchgraph.application.scope import LayoutDiagnosticService, TargetReconciler
from pyarchgraph.application.strategies import (
    CheckRegistration,
    StrategyEngine,
    StrategyRegistry,
    ViewRegistration,
)
from pyarchgraph.application.validation import OptionValidator
from pyarchgraph.domain.canonicalization import FactCanonicalizer
from pyarchgraph.domain.coverage import CoveragePolicy
from pyarchgraph.domain.graph_algorithms import GraphAlgorithms
from pyarchgraph.domain.resolution import (
    ArchitectureDependencyPolicy,
    ImportResolver,
    StaticImportResolver,
)
from pyarchgraph.domain.strategies import (
    CycleCheck,
    ModuleBodyView,
    NonTypingView,
    StructuralView,
    UnresolvedImportCheck,
)


class ApplicationFactory:
    """Explicit registrations replace defaults; omitted registrations use built-ins.

    An injected registry is used unchanged. The optional outgoing ports permit
    alternative source stores, import resolvers, and graph libraries.
    """

    def __init__(
        self,
        *,
        registry: StrategyRegistry | None = None,
        views: tuple[ViewRegistration, ...] | None = None,
        checks: tuple[CheckRegistration, ...] | None = None,
        check_selection: Mapping[str, tuple[str, ...]] | None = None,
        discovery: SourceDiscovery | None = None,
        fact_source: ImportFactSource | None = None,
        project_access: ProjectAccess | None = None,
        resolver: ImportResolver | None = None,
        graph_algorithms: GraphAlgorithms | None = None,
    ) -> None:
        if registry is not None and any(
            value is not None for value in (views, checks, check_selection)
        ):
            raise ValueError(
                "registry cannot be combined with separate strategy registrations"
            )
        self.registry = (
            registry
            if registry is not None
            else StrategyRegistry(
                self.default_views() if views is None else views,
                self.default_checks() if checks is None else checks,
                check_selection={} if check_selection is None else check_selection,
            )
        )
        self.discovery = (
            discovery if discovery is not None else FileSystemSourceDiscovery()
        )
        self.fact_source = (
            fact_source if fact_source is not None else self.create_fact_source()
        )
        self.project_access = (
            project_access if project_access is not None else FileSystemProjectAccess()
        )
        self.resolver = resolver if resolver is not None else StaticImportResolver()
        self.graph_algorithms = (
            graph_algorithms
            if graph_algorithms is not None
            else NetworkXGraphAlgorithms()
        )

    @staticmethod
    def default_views() -> tuple[ViewRegistration, ...]:
        return (
            ViewRegistration("structural", StructuralView()),
            ViewRegistration("non-typing", NonTypingView()),
            ViewRegistration("module-body", ModuleBodyView()),
        )

    @staticmethod
    def default_checks() -> tuple[CheckRegistration, ...]:
        return (
            CheckRegistration("cycles", CycleCheck()),
            CheckRegistration("unresolved-imports", UnresolvedImportCheck()),
        )

    @staticmethod
    def create_fact_source() -> AstImportFactSource:
        return AstImportFactSource(
            SourceReader(),
            PythonAstParser(),
            ModuleImportExtractor(),
            FactCanonicalizer(),
        )

    def create_analyzer(self) -> ProjectAnalyzer:
        return AnalysisService(
            project_access=self.project_access,
            catalog_builder=SourceCatalogBuilder(
                self.discovery,
                self.fact_source,
                self.project_access,
                FactCanonicalizer(),
                BindingReconciler(),
            ),
            resolver=self.resolver,
            targets=TargetReconciler(),
            layout=LayoutDiagnosticService(self.project_access),
            coverage=CoveragePolicy(),
            dependency_policy=ArchitectureDependencyPolicy(),
            strategies=StrategyEngine(self.registry, self.graph_algorithms),
            validator=OptionValidator(tuple(item.id for item in self.registry.views)),
        )

    def create_cli(self) -> CliApplication:
        gates = tuple(item.id for item in self.registry.views)
        return CliApplication(
            self.create_analyzer(),
            TomlOptionsReader(OptionValidator(gates)),
            JsonReportRenderer(),
            gates,
        )
