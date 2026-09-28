"""Package configuration is immutable and dependency reports preserve evidence."""

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import Mock

import pytest

from pyarchgraph.adapters.driven.networkx_graph import NetworkXGraphAlgorithms
from pyarchgraph.application.exceptions import AnalysisError
from pyarchgraph.application.ports.project import ProjectAccess
from pyarchgraph.application.requests import AnalysisOptions, AnalysisRequest
from pyarchgraph.application.strategies import (
    CheckRegistration,
    StrategyEngine,
    StrategyRegistry,
    ViewRegistration,
)
from pyarchgraph.application.validation import OptionValidator
from pyarchgraph.domain.graph import AnalysisSnapshot
from pyarchgraph.domain.models import (
    DependencyEdge,
    DependencyEvidence,
    ImportFact,
    ImportSyntax,
    ResolutionKind,
    SourceModule,
)
from pyarchgraph.domain.strategies import (
    CycleCheck,
    GraphViewStrategy,
    ModuleBodyView,
    NonTypingView,
    PackageView,
    StructuralView,
)
from pyarchgraph.main import ApplicationFactory


def _snapshot():
    names = ("app.one.a", "app.one.b", "app.two.c")
    sources = tuple(
        SourceModule(
            name,
            name.replace(".", "/") + ".py",
            False,
            name.rpartition(".")[0],
            import_name=name,
        )
        for name in names
    )
    facts = tuple(
        ImportFact(
            id=f"{source}:1",
            source=source,
            path=source.replace(".", "/") + ".py",
            line=1,
            column=0,
            end_line=1,
            end_column=16,
            alias_index=0,
            syntax=ImportSyntax.IMPORT,
            source_segment="import app.two.c",
            base_module="app.two.c",
            imported_name=None,
            as_name=None,
            bound_name="app",
            relative_level=0,
        )
        for source in names[:2]
    )
    return AnalysisSnapshot(
        sources,
        facts,
        tuple(
            DependencyEdge(
                fact.source,
                "app.two.c",
                (DependencyEvidence(fact.id, ResolutionKind.EXACT_MODULE),),
            )
            for fact in facts
        ),
    )


def _registry(*, max_depth=None, report_dependencies=True):
    return StrategyRegistry(
        (
            ViewRegistration("structural", StructuralView()),
            ViewRegistration(
                "package-structural",
                PackageView(StructuralView(), max_depth=max_depth),
                report_dependencies=report_dependencies,
            ),
        ),
        (CheckRegistration("cycles", CycleCheck()),),
    )


@pytest.mark.parametrize("depth", [True, False, 0, -1, 1.5, "2"])
def test_invalid_depth_is_rejected_before_project_access(depth):
    access = Mock(spec=ProjectAccess)
    analyzer = ApplicationFactory(project_access=access).create_analyzer()
    with pytest.raises(ValueError, match="package_max_depth"):
        analyzer.analyse(
            AnalysisRequest(
                (Path("not-a-real-root"),),
                options=AnalysisOptions(package_max_depth=depth),
            )
        )
    access.locate.assert_not_called()


@pytest.mark.parametrize("depth", [None, 1, 2, 100])
def test_valid_package_depth_is_accepted(depth):
    OptionValidator().validate(AnalysisOptions(package_max_depth=depth))


def test_depth_is_per_run_and_module_results_do_not_change():
    registry = _registry()
    strategy = registry.views[1].strategy
    engine = StrategyEngine(registry, NetworkXGraphAlgorithms())
    immediate = engine.evaluate(_snapshot())
    capped = engine.evaluate(_snapshot(), package_max_depth=1)
    repeated = engine.evaluate(_snapshot())

    assert len(immediate["package-structural"].nodes) == 2
    assert len(capped["package-structural"].nodes) == 1
    assert immediate["package-structural"].dependency_count == 1
    assert capped["package-structural"].dependencies == ()
    assert immediate == repeated
    assert immediate["structural"] == capped["structural"]
    assert engine.registry is registry
    assert registry.views[1].strategy is strategy
    assert strategy.max_depth is None


def test_absent_depth_preserves_explicit_constructor_configuration():
    registry = _registry(max_depth=1)
    assert registry.for_run() is registry
    engine = StrategyEngine(registry, NetworkXGraphAlgorithms())
    assert len(engine.evaluate(_snapshot())["package-structural"].nodes) == 1
    assert (
        len(
            engine.evaluate(_snapshot(), package_max_depth=2)[
                "package-structural"
            ].nodes
        )
        == 2
    )
    assert registry.views[1].strategy.max_depth == 1


def test_reported_acyclic_relationships_include_every_original_import():
    result = StrategyEngine(_registry(), NetworkXGraphAlgorithms()).evaluate(
        _snapshot()
    )
    package = result["package-structural"]
    assert package.findings == ()
    assert result["structural"].dependencies is None
    assert len(package.dependencies) == package.dependency_count == 1
    (dependency,) = package.dependencies
    labels = {node.id: node.label for node in package.nodes}
    assert (labels[dependency.source], labels[dependency.target]) == (
        "app.one",
        "app.two",
    )
    assert {item.source for item in dependency.evidence} == {"app.one.a", "app.one.b"}
    assert all(
        item.target == "app.two.c"
        and item.source_segment == "import app.two.c"
        and item.line == item.column == 1
        and item.resolution_kind is ResolutionKind.EXACT_MODULE
        and item.fact_id
        for item in dependency.evidence
    )


def test_dependency_report_flag_does_not_change_analysis():
    included = StrategyEngine(_registry(), NetworkXGraphAlgorithms()).evaluate(
        _snapshot()
    )
    omitted = StrategyEngine(
        _registry(report_dependencies=False), NetworkXGraphAlgorithms()
    ).evaluate(_snapshot())
    package = omitted["package-structural"]
    assert package.dependencies is None
    assert package.nodes == included["package-structural"].nodes
    assert package.dependency_count == 1
    assert package.findings == included["package-structural"].findings


@dataclass(frozen=True)
class CustomPackageView(PackageView):
    """External package strategies remain responsible for their own configuration."""


class ExternalView(GraphViewStrategy):
    def transform(self, snapshot):
        return StructuralView().transform(snapshot)


def test_depth_does_not_reconfigure_custom_strategies_or_subclasses():
    external = ExternalView()
    custom = CustomPackageView(StructuralView(), max_depth=2)
    registry = StrategyRegistry(
        (
            ViewRegistration("external", external, report_dependencies=True),
            ViewRegistration("custom-package", custom),
        ),
        (),
    )
    assert registry.for_run(package_max_depth=1) is registry
    reports = StrategyEngine(registry, NetworkXGraphAlgorithms()).evaluate(
        _snapshot(), package_max_depth=1
    )
    assert reports["external"].dependency_count == len(reports["external"].dependencies)
    assert len(reports["custom-package"].nodes) == 2
    assert registry.views[0].strategy is external
    assert registry.views[1].strategy is custom


@pytest.mark.parametrize(
    "view_id,source_type",
    [
        ("package-structural", StructuralView),
        ("package-non-typing", NonTypingView),
        ("package-module-body", ModuleBodyView),
    ],
)
def test_reserved_package_ids_require_their_matching_source_policy(
    view_id, source_type
):
    StrategyRegistry((ViewRegistration(view_id, PackageView(source_type())),), ())
    OptionValidator().validate(AnalysisOptions(gate=view_id))
    wrong_source = (
        NonTypingView() if source_type is StructuralView else StructuralView()
    )
    for strategy in (StructuralView(), PackageView(wrong_source)):
        with pytest.raises(AnalysisError, match="reserved"):
            StrategyRegistry((ViewRegistration(view_id, strategy),), ())


@pytest.mark.parametrize("value", [None, 0, 1, "true"])
def test_dependency_report_option_requires_an_exact_boolean(value):
    with pytest.raises(AnalysisError, match="report_dependencies"):
        _registry(report_dependencies=value)


@pytest.mark.parametrize("depth", [True, False, 0, -1, 1.5, "2"])
def test_direct_engine_calls_validate_depth(depth):
    with pytest.raises(AnalysisError, match="package_max_depth"):
        StrategyEngine(_registry(), NetworkXGraphAlgorithms()).evaluate(
            _snapshot(), package_max_depth=depth
        )
