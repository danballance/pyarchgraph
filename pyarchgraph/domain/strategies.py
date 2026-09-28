"""Analysis strategy contracts, built-ins, and cycle interpretation policy."""

from __future__ import annotations

from typing import Protocol

from pyarchgraph.domain.graph import (
    AnalysisSnapshot,
    CheckContext,
    ViewEdge,
    ViewEvidence,
    ViewGraph,
    ViewNode,
)
from pyarchgraph.domain.graph_algorithms import GraphAlgorithms, GraphHandle
from pyarchgraph.domain.models import (
    CheckResult,
    CycleFinding,
    Details,
    EvidenceLocation,
    FindingDependency,
    ImportFact,
    ImportFinding,
    ResolutionKind,
    Severity,
    UnresolvedReason,
)


class GraphViewStrategy(Protocol):
    """Define how a shared analysis snapshot becomes a graph view."""

    def transform(self, snapshot: AnalysisSnapshot) -> ViewGraph: ...


class CheckStrategy(Protocol):
    """Define how a view is inspected to produce findings with severity."""

    def evaluate(self, context: CheckContext) -> tuple[CheckResult, ...]: ...


class SourceGraphProjection:
    """Build a view with one node per source and only the chosen import facts."""

    def project(
        self, snapshot: AnalysisSnapshot, fact_ids: frozenset[str]
    ) -> ViewGraph:
        return ViewGraph(
            nodes=tuple(
                ViewNode(source.id, source.import_name or source.path, (source.id,))
                for source in snapshot.sources
            ),
            dependencies=tuple(
                ViewEdge(edge.source, edge.target, evidence)
                for edge in snapshot.dependencies
                if (
                    evidence := tuple(
                        ViewEvidence(
                            edge.source, edge.target, item.fact_id, item.resolution_kind
                        )
                        for item in edge.evidence
                        if item.fact_id in fact_ids
                    )
                )
            ),
            retained_fact_ids=tuple(sorted(fact_ids)),
        )


class StructuralView(GraphViewStrategy):
    """Include all explicit imports in a source graph view."""

    def transform(self, snapshot: AnalysisSnapshot) -> ViewGraph:
        return SourceGraphProjection().project(
            snapshot, frozenset(fact.id for fact in snapshot.facts)
        )


class NonTypingView(GraphViewStrategy):
    """Build a source graph view without recognised typing-only imports."""

    def transform(self, snapshot: AnalysisSnapshot) -> ViewGraph:
        return SourceGraphProjection().project(
            snapshot,
            frozenset(
                fact.id for fact in snapshot.facts if not fact.context.typing_only
            ),
        )


class ModuleBodyView(GraphViewStrategy):
    """Exclude recognised typing-only imports and those inside functions or methods."""

    def transform(self, snapshot: AnalysisSnapshot) -> ViewGraph:
        return SourceGraphProjection().project(
            snapshot,
            frozenset(
                fact.id
                for fact in snapshot.facts
                if not fact.context.typing_only and not fact.context.in_function
            ),
        )


class EvidenceInterpreter:
    """Produce readable source evidence and remove repeated locations per dependency."""

    @staticmethod
    def location(
        fact: ImportFact,
        kind: ResolutionKind | None = None,
        *,
        target: str | None = None,
    ) -> EvidenceLocation:
        return EvidenceLocation(
            fact.path,
            fact.line,
            fact.column + 1,
            fact.source_segment,
            kind,
            fact.context,
            source=fact.source,
            target=target,
            fact_id=fact.id,
        )

    def unique_locations(
        self, evidence: tuple[EvidenceLocation, ...]
    ) -> tuple[EvidenceLocation, ...]:
        # Multiple aliases may share one physical import site. Choose one
        # canonical fact while preserving distinct original endpoint pairs.
        unique = {}
        for item in sorted(evidence, key=self._location_with_fact_key):
            unique.setdefault(self._location_key(item), item)
        return tuple(unique.values())

    def _location_with_fact_key(self, item: EvidenceLocation) -> tuple:
        return self._location_key(item) + (item.fact_id or "",)

    @staticmethod
    def _location_key(item: EvidenceLocation) -> tuple:
        return (
            item.path,
            item.line,
            item.column,
            item.resolution_kind.value if item.resolution_kind else "",
            item.source_segment or "",
            item.context.scope,
            item.context.in_function,
            item.context.typing_only,
            item.context.conditional,
            item.context.exception_handler,
            item.context.package_initializer,
            item.source or "",
            item.target or "",
        )


class CycleAnalyzer:
    """Interpret cycle certainty and evidence, choosing one stable witness per group."""

    DEFINITE_KINDS = frozenset({ResolutionKind.EXACT_MODULE, ResolutionKind.EXACT_BASE})

    def __init__(self, algorithms: GraphAlgorithms) -> None:
        self._algorithms = algorithms
        self._evidence = EvidenceInterpreter()

    def analyze(
        self, graph: ViewGraph, facts: tuple[ImportFact, ...], *, details: Details
    ) -> tuple[CycleFinding, ...]:
        by_pair = {(edge.source, edge.target): edge for edge in graph.dependencies}
        pairs = tuple(sorted(by_pair))
        definite_pairs = tuple(
            pair
            for pair in pairs
            if any(
                item.resolution_kind in self.DEFINITE_KINDS
                for item in by_pair[pair].evidence
            )
        )
        node_ids = tuple(node.id for node in graph.nodes)
        full_handle = self._algorithms.prepare(node_ids, pairs)
        definite_handle = self._algorithms.prepare(node_ids, definite_pairs)
        components = self._cyclic_components(full_handle, frozenset(pairs))
        definite_cyclic = {
            member
            for group in self._cyclic_components(
                definite_handle, frozenset(definite_pairs)
            )
            for member in group
        }
        component_index = {
            member: index
            for index, members in enumerate(components)
            for member in members
        }
        component_pairs: list[list[tuple[str, str]]] = [[] for _ in components]
        for pair in pairs:
            index = component_index.get(pair[0])
            if index is not None and component_index.get(pair[1]) == index:
                component_pairs[index].append(pair)
        facts_by_id = {fact.id: fact for fact in facts}
        findings = []
        for index, members in enumerate(components):
            definite_members = tuple(
                member for member in members if member in definite_cyclic
            )
            definite_only = bool(definite_members)
            handle = definite_handle if definite_only else full_handle
            start = definite_members[0] if definite_only else members[0]
            witness = handle.bounded_witness(members, start=start)
            findings.append(
                CycleFinding(
                    certainty="definite" if definite_only else "possible",
                    members=members,
                    definite_members=definite_members,
                    witness=tuple(
                        self._dependency(
                            by_pair[pair], facts_by_id, definite_only=definite_only
                        )
                        for pair in witness
                    ),
                    dependency_count=len(component_pairs[index]),
                    dependencies=(
                        tuple(
                            self._dependency(
                                by_pair[pair], facts_by_id, definite_only=False
                            )
                            for pair in component_pairs[index]
                        )
                        if details == "component-edges"
                        else None
                    ),
                )
            )
        return tuple(findings)

    def _dependency(
        self, edge: ViewEdge, facts: dict[str, ImportFact], *, definite_only: bool
    ) -> FindingDependency:
        return FindingDependency(
            edge.source,
            edge.target,
            self._evidence.unique_locations(
                tuple(
                    self._evidence.location(
                        facts[item.fact_id], item.resolution_kind, target=item.target
                    )
                    for item in edge.evidence
                    if not definite_only or item.resolution_kind in self.DEFINITE_KINDS
                )
            ),
        )

    @staticmethod
    def _cyclic_components(
        handle: GraphHandle, pairs: frozenset[tuple[str, str]]
    ) -> tuple[tuple[str, ...], ...]:
        return tuple(
            sorted(
                tuple(sorted(members))
                for members in handle.strongly_connected_components()
                if len(members) > 1 or (members[0], members[0]) in pairs
            )
        )


class CycleCheck(CheckStrategy):
    """Report every analysed cycle group as an error finding."""

    def evaluate(self, context: CheckContext) -> tuple[CheckResult, ...]:
        return tuple(
            CheckResult(Severity.ERROR, finding) for finding in context.cycle_analysis
        )


class UnresolvedImportCheck(CheckStrategy):
    """Report unresolved imports as errors, except unmodelled namespace bases."""

    def evaluate(self, context: CheckContext) -> tuple[CheckResult, ...]:
        facts = {fact.id: fact for fact in context.facts}
        node_by_source = {
            member: node.id for node in context.graph.nodes for member in node.members
        }
        interpreter = EvidenceInterpreter()
        findings = []
        for unresolved in context.unresolved_imports:
            if unresolved.reason is UnresolvedReason.NAMESPACE_BASE_UNMODELLED:
                continue
            messages = {
                UnresolvedReason.MISSING_INTERNAL_TARGET: f"Internal import target {unresolved.requested!r} was not found.",
                UnresolvedReason.RELATIVE_ESCAPE: f"Relative import {unresolved.requested!r} escapes its package.",
                UnresolvedReason.UNKNOWN_PACKAGE_CONTEXT: f"Relative import {unresolved.requested!r} has no known package context.",
                UnresolvedReason.AMBIGUOUS_TARGET: f"Import target {unresolved.requested!r} has ambiguous source bindings.",
            }
            findings.append(
                CheckResult(
                    Severity.ERROR,
                    ImportFinding(
                        kind="unresolved_import",
                        source=unresolved.source,
                        requested=unresolved.requested,
                        code=unresolved.reason.value,
                        message=messages[unresolved.reason],
                        evidence=interpreter.unique_locations(
                            tuple(
                                interpreter.location(facts[fact_id])
                                for fact_id in unresolved.fact_ids
                            )
                        ),
                        node=node_by_source[unresolved.source],
                    ),
                )
            )
        return tuple(findings)
