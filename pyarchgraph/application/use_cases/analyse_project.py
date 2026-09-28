"""Sequence injected ports and policies, retaining useful partial analysis results."""

from pyarchgraph.application.catalog import SourceCatalogBuilder
from pyarchgraph.application.exceptions import AnalysisError
from pyarchgraph.application.ports.analysis import ProjectAnalyzer
from pyarchgraph.application.ports.project import ProjectAccess
from pyarchgraph.application.requests import AnalysisRequest
from pyarchgraph.application.results import AnalysisReport, Coverage
from pyarchgraph.application.scope import LayoutDiagnosticService
from pyarchgraph.application.strategies import StrategyEngine
from pyarchgraph.application.validation import OptionValidator
from pyarchgraph.domain.coverage import CoveragePolicy
from pyarchgraph.domain.graph import AnalysisSnapshot
from pyarchgraph.domain.models import Diagnostic, Severity
from pyarchgraph.domain.resolution import ArchitectureDependencyPolicy, ImportResolver
from pyarchgraph.domain.targets import TargetReconciler


class AnalyseProject(ProjectAnalyzer):
    """Reusable configuration; all mutable observations are local to an analysis."""

    def __init__(
        self,
        *,
        project_access: ProjectAccess,
        catalog_builder: SourceCatalogBuilder,
        resolver: ImportResolver,
        targets: TargetReconciler,
        layout: LayoutDiagnosticService,
        coverage: CoveragePolicy,
        dependency_policy: ArchitectureDependencyPolicy,
        strategies: StrategyEngine,
        validator: OptionValidator,
    ) -> None:
        self.project_access = project_access
        self.catalog_builder = catalog_builder
        self.resolver = resolver
        self.targets = targets
        self.layout = layout
        self.coverage = coverage
        self.dependency_policy = dependency_policy
        self.strategies = strategies
        self.validator = validator

    def analyse(self, request: AnalysisRequest) -> AnalysisReport:
        self.validator.validate_request(request)
        options = request.options
        self.validator.validate(options)
        location = self.project_access.locate(request.source_roots, request.base_dir)
        catalog = self.catalog_builder.build(location, options)
        try:
            targets = self.targets.reconcile(catalog.targets, options.targets)
        except ValueError as error:
            raise AnalysisError(str(error)) from error
        resolution = self.resolver.resolve(
            catalog.facts,
            catalog.modules,
            catalog.namespace_prefixes,
            owned_prefixes=options.owned_prefixes,
            targets=targets,
        )
        diagnostics = list(catalog.diagnostics + resolution.diagnostics)
        diagnostics.extend(
            self.targets.diagnose_layout(
                catalog.modules, resolution.boundaries, catalog.targets
            )
        )
        diagnostics.extend(
            self.layout.diagnose(location, catalog.modules, resolution.external_imports)
        )
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
        snapshot = AnalysisSnapshot(
            sources=catalog.modules,
            facts=catalog.facts,
            dependencies=self.dependency_policy.select(resolution.dependencies),
            external_imports=resolution.external_imports,
            unresolved_imports=resolution.unresolved_imports,
        )
        views = self.strategies.evaluate(snapshot, details=options.details)
        final_diagnostics = self.coverage.diagnostics(tuple(diagnostics))
        return AnalysisReport(
            status="complete"
            if self.coverage.is_complete(final_diagnostics, resolution.boundaries)
            else "incomplete",
            gate=options.gate,
            sources=catalog.modules,
            coverage=Coverage(
                roots=tuple(
                    self.project_access.relative(root, location.base_dir)
                    for root in location.roots
                ),
                excludes=catalog.effective_excludes,
                excluded_paths=tuple(
                    sorted(
                        set(catalog.excluded_paths),
                        key=lambda item: (item.path, item.rule, item.kind),
                    )
                ),
                analyzed_source_count=sum(
                    module.analysis_status == "analyzed" for module in catalog.modules
                ),
                diagnostics=final_diagnostics,
                boundaries=resolution.boundaries,
            ),
            views=views,
        )
