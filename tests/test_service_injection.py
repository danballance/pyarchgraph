"""Exercise the application through fakes rather than filesystem or graph adapters."""

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from pyarchgraph.application.analysis import AnalysisService
from pyarchgraph.application.catalog import BindingReconciler, SourceCatalogBuilder
from pyarchgraph.application.scope import LayoutDiagnosticService, TargetReconciler
from pyarchgraph.application.strategies import (
    CheckRegistration,
    StrategyEngine,
    StrategyRegistry,
    ViewRegistration,
)
from pyarchgraph.application.validation import OptionValidator
from pyarchgraph.domain.canonicalization import FactCanonicalizer
from pyarchgraph.domain.catalog import DiscoveryResult
from pyarchgraph.domain.coverage import CoveragePolicy
from pyarchgraph.domain.errors import ExtensionError
from pyarchgraph.domain.location import (
    DirectoryLocation,
    ProjectLocation,
    ProjectMetadata,
)
from pyarchgraph.domain.model import (
    AnalysisOptions,
    CheckResult,
    Diagnostic,
    FactCollection,
    ImportFact,
    ImportSyntax,
    Severity,
    SourceModule,
)
from pyarchgraph.domain.resolution import (
    ArchitectureDependencyPolicy,
    StaticImportResolver,
)
from pyarchgraph.domain.strategies import CycleCheck, StructuralView
from pyarchgraph.adapters.discovery import FileSystemSourceDiscovery
from pyarchgraph.composition import ApplicationFactory


SOURCE = SourceModule("source:sample.py", "sample.py", False, None, "sample")
FACT = ImportFact(
    "temporary",
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


class MemoryProjectAccess:
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


class MemoryDiscovery:
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


class MemoryFactSource:
    def __init__(self) -> None:
        self.collection = FactCollection((FACT,))

    def collect(
        self, source_root: Path, modules: tuple[SourceModule, ...]
    ) -> FactCollection:
        assert modules == (SOURCE,)
        return self.collection


@dataclass(frozen=True)
class SingletonGraphHandle:
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


class SingletonGraphAlgorithms:
    def __init__(self) -> None:
        self.handles: list[SingletonGraphHandle] = []

    def prepare(
        self, nodes: tuple[str, ...], edges: tuple[tuple[str, str], ...]
    ) -> SingletonGraphHandle:
        handle = SingletonGraphHandle(nodes, edges)
        self.handles.append(handle)
        return handle


class FailableCheck:
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
    analyzer = AnalysisService(
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
    analyzer, access, discovery, _, graphs = service()
    report = analyzer.analyse(
        (Path("src"),), options=AnalysisOptions(), base_dir=Path("/not-a-real-project")
    )
    assert access.calls == [((Path("src"),), Path("/not-a-real-project"))]
    assert discovery.calls == [
        (Path("/not-a-real-project/src"), ("*_test.py", "test_*.py", "tests"), ())
    ]
    assert report.coverage.roots == ("src",)
    assert report.sources[0].id == "source:src/sample.py"
    assert report.sources[0].analysis_status == "analyzed"
    assert report.status == "complete"
    assert report.exit_code == 1
    assert report.selected_view.cyclic_node_count == 1
    assert len(graphs.handles) == 2
    assert (
        report.selected_view.findings[0].finding.witness[0].evidence[0].path
        == "src/sample.py"
    )


def test_reused_analyzer_recovers_after_incomplete_source_coverage() -> None:
    analyzer, _, _, facts, _ = service()
    initial = analyzer.analyse((Path("src"),), options=AnalysisOptions())
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
    incomplete = analyzer.analyse((Path("src"),), options=AnalysisOptions())
    assert incomplete.exit_code == 2
    assert incomplete.selected_view.findings == ()
    assert incomplete.coverage.diagnostics[0].column == 1
    facts.collection = FactCollection((FACT,))
    recovered = analyzer.analyse((Path("src"),), options=AnalysisOptions())
    assert recovered == initial
    assert recovered.coverage.diagnostics == ()
    assert initial.selected_view.cyclic_node_count == 1


def test_reused_analyzer_recovers_after_extension_exception() -> None:
    check = FailableCheck()
    analyzer, _, _, _, _ = service(check=check)
    initial = analyzer.analyse((Path("src"),), options=AnalysisOptions())
    check.fail = True
    with pytest.raises(ExtensionError, match="external-rule.*structural") as failure:
        analyzer.analyse((Path("src"),), options=AnalysisOptions())
    assert isinstance(failure.value.__cause__, RuntimeError)
    check.fail = False
    assert analyzer.analyse((Path("src"),), options=AnalysisOptions()) == initial


def test_unknown_gate_is_rejected_before_outgoing_ports() -> None:
    analyzer, access, discovery, _, graphs = service()
    with pytest.raises(ValueError):
        analyzer.analyse((Path("src"),), options=AnalysisOptions(gate="missing"))
    assert access.calls == []
    assert discovery.calls == []
    assert graphs.handles == []


def test_captured_base_survives_cwd_changes_and_reused_service_root_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    source = project / "src"
    source.mkdir(parents=True)
    (source / "sample.py").write_text("import sample\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    class RelocatingDiscovery:
        def discover(self, root, *, excludes=(), pruned_directories=()):
            result = FileSystemSourceDiscovery().discover(
                root, excludes=excludes, pruned_directories=pruned_directories
            )
            monkeypatch.chdir(elsewhere)
            return result

    analyzer = ApplicationFactory(discovery=RelocatingDiscovery()).create_analyzer()
    monkeypatch.chdir(project)
    with patch.object(Path, "cwd", wraps=Path.cwd) as cwd:
        initial = analyzer.analyse((Path("src"),), options=AnalysisOptions())
        assert cwd.call_count == 1
    assert Path.cwd() == elsewhere
    assert initial.coverage.roots == ("src",)
    assert initial.sources[0].path == "src/sample.py"
    assert initial.exit_code == 1

    with patch.object(Path, "cwd", side_effect=AssertionError("unexpected cwd read")):
        explicit = analyzer.analyse(
            (Path("src"),), options=AnalysisOptions(), base_dir=project
        )
        assert explicit == initial
        with pytest.raises(ValueError, match="existing directory"):
            analyzer.analyse(
                (Path("missing"),), options=AnalysisOptions(), base_dir=project
            )
        assert (
            analyzer.analyse(
                (Path("src"),), options=AnalysisOptions(), base_dir=project
            )
            == initial
        )
