"""The sole composition root for concrete runtime adapters."""

from collections.abc import Mapping

from pyarchgraph.adapters.driven.filesystem.discovery import FileSystemSourceDiscovery
from pyarchgraph.adapters.driven.filesystem.project import FileSystemProjectAccess
from pyarchgraph.adapters.driven.networkx_graph import NetworkXGraphAlgorithms
from pyarchgraph.adapters.driven.python_ast import (
    AstImportFactSource,
    ModuleImportExtractor,
    PythonAstParser,
    SourceReader,
)
from pyarchgraph.adapters.driving.cli.application import CliApplication
from pyarchgraph.adapters.driving.cli.configuration import TomlOptionsReader
from pyarchgraph.adapters.driving.cli.rendering import JsonReportRenderer
from pyarchgraph.application.catalog import SourceCatalogBuilder
from pyarchgraph.application.ports.analysis import ProjectAnalyzer
from pyarchgraph.application.ports.project import ProjectAccess
from pyarchgraph.application.ports.sources import ImportFactSource, SourceDiscovery
from pyarchgraph.application.scope import LayoutDiagnosticService
from pyarchgraph.application.strategies import (
    CheckRegistration,
    StrategyEngine,
    StrategyRegistry,
    ViewRegistration,
)
from pyarchgraph.application.use_cases.analyse_project import AnalyseProject
from pyarchgraph.application.validation import OptionValidator
from pyarchgraph.domain.bindings import BindingReconciler
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
from pyarchgraph.domain.targets import TargetReconciler


class ApplicationFactory:
    """Connect built-in or supplied services and adapters into an analyser or CLI.

    Explicit registrations replace built-ins; an injected registry is used unchanged.
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
        )

    def create_analyzer(self) -> ProjectAnalyzer:
        return AnalyseProject(
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
            TomlOptionsReader(),
            JsonReportRenderer(),
            gates,
        )
