"""Exercise the application through fakes rather than filesystem or graph adapters."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from pyarchgraph.application.catalog import SourceCatalogBuilder
from pyarchgraph.application.exceptions import ExtensionError
from pyarchgraph.application.ports.project import (
    DirectoryLocation,
    ProjectAccess,
    ProjectLocation,
    ProjectMetadata,
)
from pyarchgraph.application.ports.sources import (
    DiscoveryResult,
    FactCollection,
    ImportFactSource,
    SourceDiscovery,
)
from pyarchgraph.application.requests import AnalysisOptions, AnalysisRequest
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
from pyarchgraph.domain.graph_algorithms import GraphAlgorithms, GraphHandle
from pyarchgraph.domain.models import (
    CheckResult,
    Diagnostic,
    ImportFactDraft,
    ImportSyntax,
    Severity,
    SourceModule,
)
from pyarchgraph.domain.resolution import (
    ArchitectureDependencyPolicy,
    StaticImportResolver,
)
from pyarchgraph.domain.strategies import CheckStrategy, CycleCheck, StructuralView
from pyarchgraph.domain.targets import TargetReconciler

SOURCE = SourceModule("source:sample.py", "sample.py", False, None, "sample")
FACT = ImportFactDraft(
    SOURCE.id,
    SOURCE.path,
    1,
    0,
    1,
    13,
    0,
    ImportSyntax.IMPORT,
    "import sample",
    "sample",
    None,
    None,
    "sample",
    0,
)


class MemoryProjectAccess(ProjectAccess):
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Path, ...], Path | None]] = []

    def locate(
        self, source_roots: tuple[Path, ...], base_dir: Path | None
    ) -> ProjectLocation:
        self.calls.append((source_roots, base_dir))
        base = base_dir if base_dir is not None else Path("/virtual")
        return ProjectLocation(base, tuple(base / root for root in source_roots))

    def relative(self, path: Path, base_dir: Path) -> str:
        return path.relative_to(base_dir).as_posix()

    def absolute(self, path: str, base_dir: Path) -> Path:
        return base_dir / path

    def read_metadata(self, root: Path) -> ProjectMetadata:
        return ProjectMetadata()

    def directory(self, root: Path, candidate: str) -> DirectoryLocation:
        return DirectoryLocation(root / candidate, False)


class MemoryDiscovery(SourceDiscovery):
    def __init__(self) -> None:
        self.calls: list[tuple[Path, tuple[str, ...], tuple[str, ...]]] = []

    def discover(
        self,
        source_root: Path,
        *,
        excludes: tuple[str, ...] = (),
        pruned_directories: tuple[str, ...] = (),
    ) -> DiscoveryResult:
        self.calls.append((source_root, excludes, pruned_directories))
        return DiscoveryResult((SOURCE,), (), ())


class MemoryFactSource(ImportFactSource):
    def __init__(self) -> None:
        self.collection = FactCollection((FACT,))
        self.calls = []

    def collect(
        self, source_root: Path, modules: tuple[SourceModule, ...]
    ) -> FactCollection:
        self.calls.append((source_root, modules))
        assert modules == (SOURCE,)
        return self.collection


@dataclass(frozen=True)
class SingletonGraphHandle(GraphHandle):
    nodes: tuple[str, ...]
    edges: tuple[tuple[str, str], ...]

    def strongly_connected_components(self) -> tuple[tuple[str, ...], ...]:
        return tuple((node,) for node in self.nodes)

    def bounded_witness(
        self, members: tuple[str, ...], *, start: str
    ) -> tuple[tuple[str, str], ...]:
        assert members == (start,)
        assert (start, start) in self.edges
        return ((start, start),)


class SingletonGraphAlgorithms(GraphAlgorithms):
    def __init__(self) -> None:
        self.handles: list[SingletonGraphHandle] = []

    def prepare(
        self, nodes: tuple[str, ...], edges: tuple[tuple[str, str], ...]
    ) -> SingletonGraphHandle:
        handle = SingletonGraphHandle(nodes, edges)
        self.handles.append(handle)
        return handle


class FailableCheck(CheckStrategy):
    def __init__(self) -> None:
        self.fail = False

    def evaluate(self, context) -> tuple[CheckResult, ...]:
        if self.fail:
            raise RuntimeError("injected failure")
        return ()


def service(*, check=None):
    access = MemoryProjectAccess()
    discovery = MemoryDiscovery()
    facts = MemoryFactSource()
    graphs = SingletonGraphAlgorithms()
    checks = (CheckRegistration("cycles", CycleCheck()),)
    if check is not None:
        checks += (CheckRegistration("external-rule", check),)
    registry = StrategyRegistry(
        (ViewRegistration("structural", StructuralView()),), checks
    )
    analyzer = AnalyseProject(
        project_access=access,
        catalog_builder=SourceCatalogBuilder(
            discovery, facts, access, FactCanonicalizer(), BindingReconciler()
        ),
        resolver=StaticImportResolver(),
        targets=TargetReconciler(),
        layout=LayoutDiagnosticService(access),
        coverage=CoveragePolicy(),
        dependency_policy=ArchitectureDependencyPolicy(),
        strategies=StrategyEngine(registry, graphs),
        validator=OptionValidator(("structural",)),
    )
    return analyzer, access, discovery, facts, graphs


def test_core_uses_injected_ports_and_explicit_base_dir() -> None:
    analyzer, access, discovery, facts, graphs = service()
    report = analyzer.analyse(
        AnalysisRequest(
            (Path("src"),),
            options=AnalysisOptions(),
            base_dir=Path("/not-a-real-project"),
        )
    )
    assert access.calls == [((Path("src"),), Path("/not-a-real-project"))]
    assert discovery.calls == [
        (Path("/not-a-real-project/src"), ("*_test.py", "test_*.py", "tests"), ())
    ]
    assert report.coverage.roots == ("src",)
    assert report.sources[0].id == "source:src/sample.py"
    assert report.sources[0].analysis_status == "analyzed"
    assert report.status == "complete"
    assert report.selected_view.findings[0].severity is Severity.ERROR
    assert report.selected_view.cyclic_node_count == 1
    assert len(graphs.handles) == 2
    assert (
        report.selected_view.findings[0].finding.witness[0].evidence[0].path
        == "src/sample.py"
    )


def test_reused_analyzer_recovers_after_incomplete_source_coverage() -> None:
    analyzer, _, _, facts, _ = service()
    initial = analyzer.analyse(
        AnalysisRequest((Path("src"),), options=AnalysisOptions())
    )
    facts.collection = FactCollection(
        (),
        (
            Diagnostic(
                Severity.ERROR,
                "source_syntax_error",
                "Could not parse source.",
                "sample.py",
                1,
                0,
            ),
        ),
    )
    incomplete = analyzer.analyse(
        AnalysisRequest((Path("src"),), options=AnalysisOptions())
    )
    assert incomplete.status == "incomplete"
    assert incomplete.selected_view.findings == ()
    assert incomplete.coverage.diagnostics[0].column == 1
    facts.collection = FactCollection((FACT,))
    recovered = analyzer.analyse(
        AnalysisRequest((Path("src"),), options=AnalysisOptions())
    )
    assert recovered == initial
    assert recovered.coverage.diagnostics == ()
    assert initial.selected_view.cyclic_node_count == 1


def test_reused_analyzer_recovers_after_extension_exception() -> None:
    check = FailableCheck()
    analyzer, _, _, _, _ = service(check=check)
    initial = analyzer.analyse(
        AnalysisRequest((Path("src"),), options=AnalysisOptions())
    )
    check.fail = True
    with pytest.raises(ExtensionError, match="external-rule.*structural") as failure:
        analyzer.analyse(AnalysisRequest((Path("src"),), options=AnalysisOptions()))
    assert isinstance(failure.value.__cause__, RuntimeError)
    check.fail = False
    assert (
        analyzer.analyse(AnalysisRequest((Path("src"),), options=AnalysisOptions()))
        == initial
    )


def test_unknown_gate_is_rejected_before_outgoing_ports() -> None:
    analyzer, access, discovery, facts, graphs = service()
    with pytest.raises(ValueError):
        analyzer.analyse(
            AnalysisRequest((Path("src"),), options=AnalysisOptions(gate="missing"))
        )
    assert access.calls == []
    assert discovery.calls == []
    assert facts.calls == []
    assert graphs.handles == []


@pytest.mark.parametrize(
    "analysis_request",
    [
        None,
        AnalysisRequest(()),
        AnalysisRequest([Path("src")]),
        AnalysisRequest(("src",)),
        AnalysisRequest((Path("src"),), base_dir="/virtual"),
        AnalysisRequest((Path("src"),), options=None),
        AnalysisRequest((Path("src"),), options=AnalysisOptions(details="all")),
    ],
)
def test_invalid_request_is_rejected_before_any_outgoing_port(analysis_request) -> None:
    analyzer, access, discovery, facts, graphs = service()
    with pytest.raises(ValueError):
        analyzer.analyse(analysis_request)
    assert access.calls == []
    assert discovery.calls == []
    assert facts.calls == []
    assert graphs.handles == []


def test_catalog_assigns_ids_once_after_rebasing_all_root_drafts() -> None:
    class RecordingCanonicalizer(FactCanonicalizer):
        def __init__(self):
            self.inputs = []

        def canonicalise(self, facts):
            drafts = tuple(facts)
            self.inputs.append(drafts)
            return super().canonicalise(drafts)

    canonicalizer = RecordingCanonicalizer()
    builder = SourceCatalogBuilder(
        MemoryDiscovery(),
        MemoryFactSource(),
        MemoryProjectAccess(),
        canonicalizer,
        BindingReconciler(),
    )
    base = Path("/virtual")
    roots = (base / "first", base / "second")
    catalog = builder.build(ProjectLocation(base, roots), AnalysisOptions())

    assert len(canonicalizer.inputs) == 1
    drafts = canonicalizer.inputs[0]
    assert all(isinstance(draft, ImportFactDraft) for draft in drafts)
    assert all(not hasattr(draft, "id") for draft in drafts)
    assert {draft.source for draft in drafts} == {
        "source:first/sample.py",
        "source:second/sample.py",
    }
    assert {draft.path for draft in drafts} == {
        "first/sample.py",
        "second/sample.py",
    }
    assert len({fact.id for fact in catalog.facts}) == 2
    assert catalog.facts == FactCanonicalizer().canonicalise(drafts)

    reversed_catalog = builder.build(
        ProjectLocation(base, tuple(reversed(roots))), AnalysisOptions()
    )
    assert len(canonicalizer.inputs) == 2
    assert reversed_catalog.facts == catalog.facts
    assert not hasattr(FACT, "id")
