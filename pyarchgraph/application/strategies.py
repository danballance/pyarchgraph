"""Immutable extension registration and strategy orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from pyarchgraph.application.exceptions import AnalysisError, ExtensionError
from pyarchgraph.application.results import RegisteredFinding, ViewReport
from pyarchgraph.domain.graph import AnalysisSnapshot, CheckContext
from pyarchgraph.domain.graph_algorithms import GraphAlgorithms
from pyarchgraph.domain.models import Details
from pyarchgraph.domain.strategies import (
    CheckStrategy,
    CycleAnalyzer,
    CycleCheck,
    GraphViewStrategy,
    ModuleBodyView,
    NonTypingView,
    StructuralView,
    UnresolvedImportCheck,
)
from pyarchgraph.domain.validation import (
    CheckResultValidator,
    GraphValidator,
    SnapshotNormalizer,
)


@dataclass(frozen=True, slots=True)
class ViewRegistration:
    id: str
    strategy: GraphViewStrategy


@dataclass(frozen=True, slots=True)
class CheckRegistration:
    id: str
    strategy: CheckStrategy
    applicable_views: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class StrategyRegistry:
    views: tuple[ViewRegistration, ...]
    checks: tuple[CheckRegistration, ...]
    check_selection: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            self._initialize()
        except AnalysisError:
            raise
        except (AttributeError, TypeError, ValueError) as error:
            raise AnalysisError(f"invalid strategy registration: {error}") from error

    def _initialize(self) -> None:
        if not isinstance(self.check_selection, Mapping):
            raise AnalysisError(
                "check selection must be a mapping of view IDs to check ID sequences"
            )
        if isinstance(self.views, (str, bytes)) or isinstance(
            self.checks, (str, bytes)
        ):
            raise AnalysisError(
                "view and check registrations must be sequences of registrations"
            )
        if any(
            isinstance(value, (str, bytes)) for value in self.check_selection.values()
        ):
            raise AnalysisError(
                "each check selection must be a sequence of check IDs, not a string"
            )
        object.__setattr__(self, "views", tuple(self.views))
        object.__setattr__(self, "checks", tuple(self.checks))
        object.__setattr__(
            self,
            "check_selection",
            MappingProxyType(
                {key: tuple(value) for key, value in self.check_selection.items()}
            ),
        )
        self._validate_registrations()

    def validate_gate(self, gate: str) -> None:
        if type(gate) is not str or gate not in {view.id for view in self.views}:
            raise AnalysisError(f"unknown gate {gate!r}; choose a registered view ID")

    def selected_checks(self, view_id: str) -> tuple[CheckRegistration, ...]:
        selected = self.check_selection.get(view_id)
        if selected is None:
            return tuple(
                check for check in self.checks if self._applicable(check, view_id)
            )
        by_id = {check.id: check for check in self.checks}
        return tuple(by_id[check_id] for check_id in selected)

    def _validate_registrations(self) -> None:
        reserved_views = {
            "structural": StructuralView,
            "non-typing": NonTypingView,
            "module-body": ModuleBodyView,
        }
        reserved_checks = {
            "cycles": CycleCheck,
            "unresolved-imports": UnresolvedImportCheck,
        }
        for entries, expected, method, reserved in (
            (self.views, ViewRegistration, "transform", reserved_views),
            (self.checks, CheckRegistration, "evaluate", reserved_checks),
        ):
            seen = set()
            for entry in entries:
                if not isinstance(entry, expected):
                    raise AnalysisError(f"expected {expected.__name__} entries")
                if (
                    type(entry.id) is not str
                    or not entry.id.strip()
                    or entry.id != entry.id.strip()
                    or entry.id.startswith("_")
                ):
                    raise AnalysisError(
                        "extension IDs must be nonempty, trimmed, and not start with '_'"
                    )
                if entry.id in seen:
                    raise AnalysisError(f"duplicate extension ID {entry.id!r}")
                seen.add(entry.id)
                if (
                    entry.id in reserved
                    and type(entry.strategy) is not reserved[entry.id]
                ):
                    raise AnalysisError(f"reserved extension ID {entry.id!r}")
                if not callable(getattr(entry.strategy, method, None)):
                    raise AnalysisError(
                        f"extension {entry.id!r} must implement {method}()"
                    )
        view_ids = {view.id for view in self.views}
        check_ids = {check.id for check in self.checks}
        for check in self.checks:
            if check.applicable_views is not None:
                if not isinstance(check.applicable_views, tuple):
                    raise AnalysisError(
                        f"check {check.id!r} applicability must be an immutable tuple"
                    )
                if not all(type(view_id) is str for view_id in check.applicable_views):
                    raise AnalysisError(
                        f"check {check.id!r} applicable view IDs must be strings"
                    )
                if len(set(check.applicable_views)) != len(check.applicable_views):
                    raise AnalysisError(
                        f"check {check.id!r} has duplicate applicable views"
                    )
                unknown = set(check.applicable_views) - view_ids
                if unknown:
                    raise AnalysisError(
                        f"check {check.id!r} references unknown views: {sorted(unknown)!r}"
                    )
        checks = {check.id: check for check in self.checks}
        for view_id, selected in self.check_selection.items():
            if type(view_id) is not str or not all(
                type(check_id) is str for check_id in selected
            ):
                raise AnalysisError(
                    "check selection view and check IDs must be strings"
                )
            if view_id not in view_ids:
                raise AnalysisError(
                    f"check selection references unknown view {view_id!r}"
                )
            if len(set(selected)) != len(selected):
                raise AnalysisError(f"view {view_id!r} selects duplicate checks")
            unknown = set(selected) - check_ids
            if unknown:
                raise AnalysisError(
                    f"view {view_id!r} selects unknown checks: {sorted(unknown)!r}"
                )
            for check_id in selected:
                if not self._applicable(checks[check_id], view_id):
                    raise AnalysisError(
                        f"check {check_id!r} is not applicable to view {view_id!r}"
                    )

    @staticmethod
    def _applicable(check: CheckRegistration, view_id: str) -> bool:
        return check.applicable_views is None or view_id in check.applicable_views


class StrategyEngine:
    def __init__(
        self, registry: StrategyRegistry, graph_algorithms: GraphAlgorithms
    ) -> None:
        self.registry = registry
        self._cycles = CycleAnalyzer(graph_algorithms)
        self._graphs = GraphValidator()
        self._results = CheckResultValidator()

    def evaluate(
        self, snapshot: AnalysisSnapshot, *, details: Details = "summary"
    ) -> Mapping[str, ViewReport]:
        if details not in ("summary", "component-edges"):
            raise AnalysisError("details must be 'summary' or 'component-edges'")
        snapshot = SnapshotNormalizer().normalize(snapshot)
        graphs = self._graphs.prepare(snapshot)
        views = {}
        for registration in self.registry.views:
            try:
                graph = graphs.normalize(registration.strategy.transform(snapshot))
            except Exception as error:
                raise ExtensionError(
                    f"view extension {registration.id!r} failed in view {registration.id!r}: {error}"
                ) from error
            retained = frozenset(graph.retained_fact_ids)
            facts = tuple(fact for fact in snapshot.facts if fact.id in retained)
            cycles = self._cycles.analyze(graph, facts, details=details)
            context = CheckContext(
                graph=graph,
                facts=facts,
                sources=snapshot.sources,
                external_imports=tuple(
                    item if ids == item.fact_ids else replace(item, fact_ids=ids)
                    for item in snapshot.external_imports
                    if (
                        ids := tuple(
                            fact_id for fact_id in item.fact_ids if fact_id in retained
                        )
                    )
                ),
                unresolved_imports=tuple(
                    item if ids == item.fact_ids else replace(item, fact_ids=ids)
                    for item in snapshot.unresolved_imports
                    if (
                        ids := tuple(
                            fact_id for fact_id in item.fact_ids if fact_id in retained
                        )
                    )
                ),
                cycle_analysis=cycles,
                details=details,
            )
            selected = self.registry.selected_checks(registration.id)
            findings = []
            for check in selected:
                try:
                    results = check.strategy.evaluate(context)
                    self._results.validate(results, context)
                except Exception as error:
                    raise ExtensionError(
                        f"check extension {check.id!r} failed in view {registration.id!r}: {error}"
                    ) from error
                findings.extend(
                    RegisteredFinding(check.id, result.severity, result.finding)
                    for result in results
                )
            views[registration.id] = ViewReport(
                nodes=graph.nodes,
                enabled_check_ids=tuple(check.id for check in selected),
                dependency_count=len(graph.dependencies),
                cyclic_dependency_count=sum(cycle.dependency_count for cycle in cycles),
                cyclic_node_count=sum(len(cycle.members) for cycle in cycles),
                findings=tuple(findings),
            )
        return MappingProxyType(views)
