"""Immutable extension registration and strategy orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from pyarchgraph.domain.errors import AnalysisError, ExtensionError
from pyarchgraph.domain.graph import AnalysisSnapshot, CheckContext, GraphValidator
from pyarchgraph.domain.graph_algorithms import GraphAlgorithms
from pyarchgraph.domain.model import (
    CheckResult,
    CycleFinding,
    Details,
    EvidenceLocation,
    GraphView,
    FindingDependency,
    ImportContext,
    ImportFact,
    ResolutionKind,
    ImportFinding,
    RegisteredFinding,
    RuleFinding,
    Severity,
)
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
    ) -> Mapping[str, GraphView]:
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
            views[registration.id] = GraphView(
                nodes=graph.nodes,
                enabled_check_ids=tuple(check.id for check in selected),
                dependency_count=len(graph.dependencies),
                cyclic_dependency_count=sum(cycle.dependency_count for cycle in cycles),
                cyclic_node_count=sum(len(cycle.members) for cycle in cycles),
                findings=tuple(findings),
            )
        return MappingProxyType(views)


class SnapshotNormalizer:
    def normalize(self, snapshot: AnalysisSnapshot) -> AnalysisSnapshot:
        sources = tuple(sorted(snapshot.sources, key=self._id))
        facts = tuple(sorted(snapshot.facts, key=self._id))
        dependencies = tuple(
            sorted(
                (self._dependency(item) for item in snapshot.dependencies),
                key=self._edge,
            )
        )
        external = tuple(
            sorted(
                (self._record(item) for item in snapshot.external_imports),
                key=self._external,
            )
        )
        unresolved = tuple(
            sorted(
                (self._record(item) for item in snapshot.unresolved_imports),
                key=self._unresolved,
            )
        )
        if (
            type(snapshot) is AnalysisSnapshot
            and all(
                type(value) is tuple
                for value in (
                    snapshot.sources,
                    snapshot.facts,
                    snapshot.dependencies,
                    snapshot.external_imports,
                    snapshot.unresolved_imports,
                )
            )
            and sources == snapshot.sources
            and facts == snapshot.facts
            and dependencies == snapshot.dependencies
            and external == snapshot.external_imports
            and unresolved == snapshot.unresolved_imports
        ):
            return snapshot
        return AnalysisSnapshot(sources, facts, dependencies, external, unresolved)

    def _dependency(self, edge):
        evidence = (
            tuple(edge.evidence)
            if len(edge.evidence) < 2
            else tuple(sorted(set(edge.evidence), key=self._evidence))
        )
        return (
            edge
            if type(edge.evidence) is tuple and evidence == edge.evidence
            else replace(edge, evidence=evidence)
        )

    @staticmethod
    def _record(item):
        ids = (
            tuple(item.fact_ids)
            if len(item.fact_ids) < 2
            else tuple(sorted(set(item.fact_ids)))
        )
        return (
            item
            if type(item.fact_ids) is tuple and ids == item.fact_ids
            else replace(item, fact_ids=ids)
        )

    @staticmethod
    def _id(item):
        return item.id

    @staticmethod
    def _edge(item):
        return item.source, item.target

    @staticmethod
    def _evidence(item):
        return item.fact_id, item.resolution_kind.value

    @staticmethod
    def _external(item):
        return item.source, item.requested, item.classification.value

    @staticmethod
    def _unresolved(item):
        return item.source, item.requested, item.reason.value


class CheckResultValidator:
    """Keep extension payloads within the immutable public report schema."""

    def validate(self, results: tuple[CheckResult, ...], context: CheckContext) -> None:
        if type(results) is not tuple:
            raise ValueError(
                "a check must return an immutable tuple of CheckResult values"
            )
        if not results:
            return
        for result in results:
            if type(result) is not CheckResult or type(result.severity) is not Severity:
                raise ValueError(
                    "a check must return CheckResult values with a Severity"
                )
        # The common cycle check returns domain-created objects. Their shapes
        # and evidence are already known, so avoid rebuilding provenance indexes.
        cycle_ids = {id(cycle) for cycle in context.cycle_analysis}
        if all(
            type(result.finding) is CycleFinding and id(result.finding) in cycle_ids
            for result in results
        ):
            return
        nodes = {node.id for node in context.graph.nodes}
        sources = {source.id for source in context.sources}
        owner = {
            member: node.id for node in context.graph.nodes for member in node.members
        }
        facts = {fact.id: fact for fact in context.facts}
        support = {}
        for edge in context.graph.dependencies:
            for item in edge.evidence:
                support.setdefault(item.fact_id, []).append(item)
        unresolved = {}
        for item in context.unresolved_imports:
            unresolved.setdefault(
                (item.source, item.requested, item.reason.value), set()
            ).update(item.fact_ids)
        for result in results:
            finding = result.finding
            if type(finding) is RuleFinding:
                if type(finding.kind) is not str or finding.kind != "rule":
                    raise ValueError("rule findings require the 'rule' kind")
                self._validate_message(finding.code, finding.message)
                self._validate_ids(finding.node_ids, "node")
                self._validate_ids(finding.source_ids, "source")
                if not set(finding.node_ids) <= nodes:
                    raise ValueError(
                        "rule finding node references must be existing node IDs"
                    )
                if not set(finding.source_ids) <= sources:
                    raise ValueError(
                        "rule finding source references must be original source IDs"
                    )
                self._validate_evidence(finding.evidence, facts, support)
            elif type(finding) is CycleFinding:
                if id(finding) not in cycle_ids:
                    self._validate_cycle_shape(finding)
                    if finding not in context.cycle_analysis:
                        raise ValueError(
                            "cycle findings must be supported by the view's cycle analysis"
                        )
            elif type(finding) is ImportFinding:
                if type(finding.kind) is not str or finding.kind != "unresolved_import":
                    raise ValueError(
                        "unresolved findings require the 'unresolved_import' kind"
                    )
                self._validate_message(finding.code, finding.message)
                if type(finding.source) is not str or type(finding.node) is not str:
                    raise ValueError(
                        "unresolved finding source and node IDs must be strings"
                    )
                if finding.requested is not None and type(finding.requested) is not str:
                    raise ValueError(
                        "unresolved finding requested names must be strings"
                    )
                if finding.source not in owner or finding.node != owner[finding.source]:
                    raise ValueError(
                        "unresolved findings require their original source and view node"
                    )
                allowed_fact_ids = unresolved.get(
                    (finding.source, finding.requested, finding.code)
                )
                if allowed_fact_ids is None:
                    raise ValueError(
                        "unresolved findings must reference retained resolution records"
                    )
                self._validate_evidence(
                    finding.evidence, facts, support, allowed_fact_ids=allowed_fact_ids
                )
            else:
                raise ValueError(
                    "unsupported finding payload; use the public RuleFinding type for custom rules"
                )

    @staticmethod
    def _validate_message(code: str, message: str) -> None:
        if type(code) is not str or not code or type(message) is not str or not message:
            raise ValueError("findings require a nonempty string code and message")

    @staticmethod
    def _validate_ids(ids: tuple[str, ...], description: str) -> None:
        if type(ids) is not tuple or not all(
            type(item) is str and item for item in ids
        ):
            raise ValueError(
                f"finding {description} references must be immutable string IDs"
            )

    def _validate_cycle_shape(self, finding: CycleFinding) -> None:
        if type(finding.kind) is not str or finding.kind != "cycle":
            raise ValueError("cycle findings require the 'cycle' kind")
        if type(finding.certainty) is not str or finding.certainty not in (
            "definite",
            "possible",
        ):
            raise ValueError("cycle certainty must be 'definite' or 'possible'")
        if type(finding.dependency_count) is not int or finding.dependency_count < 0:
            raise ValueError("cycle dependency counts must be nonnegative integers")
        self._validate_ids(finding.members, "member")
        self._validate_ids(finding.definite_members, "definite member")
        dependency_groups = (finding.witness,)
        if finding.dependencies is not None:
            dependency_groups += (finding.dependencies,)
        for dependencies in dependency_groups:
            if type(dependencies) is not tuple:
                raise ValueError("cycle dependencies must be immutable tuples")
            for edge in dependencies:
                if (
                    type(edge) is not FindingDependency
                    or type(edge.source) is not str
                    or type(edge.target) is not str
                ):
                    raise ValueError(
                        "cycle dependencies must be public FindingDependency values with string endpoints"
                    )
                self._validate_evidence_shape(edge.evidence)

    @staticmethod
    def _validate_evidence_shape(evidence: tuple[EvidenceLocation, ...]) -> None:
        if type(evidence) is not tuple or not all(
            type(item) is EvidenceLocation for item in evidence
        ):
            raise ValueError(
                "finding evidence must be an immutable tuple of public EvidenceLocation values"
            )
        for item in evidence:
            if type(item.path) is not str or not item.path:
                raise ValueError("evidence paths must be nonempty strings")
            if (
                type(item.line) is not int
                or item.line < 1
                or type(item.column) is not int
                or item.column < 1
            ):
                raise ValueError("evidence positions must be positive integers")
            if item.source_segment is not None and type(item.source_segment) is not str:
                raise ValueError("evidence source segments must be strings")
            if (
                item.resolution_kind is not None
                and type(item.resolution_kind) is not ResolutionKind
            ):
                raise ValueError(
                    "evidence resolution kinds must be ResolutionKind values"
                )
            for reference in (item.source, item.target, item.fact_id):
                if reference is not None and (
                    type(reference) is not str or not reference
                ):
                    raise ValueError("evidence references must be nonempty string IDs")
            context = item.context
            if type(context) is not ImportContext:
                raise ValueError(
                    "evidence contexts must be public ImportContext values"
                )
            if type(context.scope) is not str or context.scope not in (
                "module",
                "class",
                "function",
            ):
                raise ValueError("evidence context scope is invalid")
            if not all(
                type(value) is bool
                for value in (
                    context.in_function,
                    context.typing_only,
                    context.conditional,
                    context.exception_handler,
                    context.package_initializer,
                )
            ):
                raise ValueError("evidence context flags must be booleans")

    def _validate_evidence(
        self,
        evidence: tuple[EvidenceLocation, ...],
        facts: dict[str, ImportFact],
        support: dict,
        *,
        allowed_fact_ids: set[str] | None = None,
    ) -> None:
        self._validate_evidence_shape(evidence)
        for item in evidence:
            candidates = (facts[item.fact_id],) if item.fact_id in facts else ()
            if item.fact_id is None:
                candidates = tuple(facts.values())
            matched = tuple(
                fact
                for fact in candidates
                if (
                    item.path,
                    item.line,
                    item.column,
                    item.source_segment,
                    item.context,
                )
                == (
                    fact.path,
                    fact.line,
                    fact.column + 1,
                    fact.source_segment,
                    fact.context,
                )
                and (item.source is None or item.source == fact.source)
                and (allowed_fact_ids is None or fact.id in allowed_fact_ids)
            )
            if not matched:
                raise ValueError(
                    "finding evidence must match a retained original import fact"
                )
            if item.target is not None or item.resolution_kind is not None:
                if not any(
                    edge.fact_id == fact.id
                    and edge.source == fact.source
                    and (item.target is None or item.target == edge.target)
                    and (
                        item.resolution_kind is None
                        or item.resolution_kind is edge.resolution_kind
                    )
                    for fact in matched
                    for edge in support.get(fact.id, ())
                ):
                    raise ValueError(
                        "finding evidence endpoints and resolution must match retained graph support"
                    )
