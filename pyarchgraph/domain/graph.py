"""Immutable graph projections and their provenance rules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from pyarchgraph.domain.model import (
    CycleFinding,
    DependencyEdge,
    Details,
    ExternalImport,
    ImportFact,
    ResolutionKind,
    SourceModule,
    UnresolvedImport,
)


@dataclass(frozen=True, slots=True)
class ViewNode:
    id: str
    label: str
    members: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ViewEvidence:
    """The original endpoints remain meaningful after projection."""

    source: str
    target: str
    fact_id: str
    resolution_kind: ResolutionKind


@dataclass(frozen=True, slots=True)
class ViewEdge:
    source: str
    target: str
    evidence: tuple[ViewEvidence, ...]


@dataclass(frozen=True, slots=True)
class ViewGraph:
    nodes: tuple[ViewNode, ...]
    dependencies: tuple[ViewEdge, ...]
    retained_fact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AnalysisSnapshot:
    sources: tuple[SourceModule, ...]
    facts: tuple[ImportFact, ...]
    dependencies: tuple[DependencyEdge, ...]
    external_imports: tuple[ExternalImport, ...] = ()
    unresolved_imports: tuple[UnresolvedImport, ...] = ()


@dataclass(frozen=True, slots=True)
class CheckContext:
    graph: ViewGraph
    facts: tuple[ImportFact, ...]
    external_imports: tuple[ExternalImport, ...]
    unresolved_imports: tuple[UnresolvedImport, ...]
    sources: tuple[SourceModule, ...]
    cycle_analysis: tuple[CycleFinding, ...]
    details: Details = "summary"


class GraphValidator:
    """Validate support before a projection is consumed by any check."""

    def prepare(self, snapshot: AnalysisSnapshot) -> _GraphValidationSession:
        """Build immutable provenance indexes once for this analysis only."""
        return _GraphValidationSession(
            source_ids=frozenset(source.id for source in snapshot.sources),
            facts=MappingProxyType({fact.id: fact for fact in snapshot.facts}),
            support=frozenset(
                (edge.source, edge.target, item.fact_id, item.resolution_kind)
                for edge in snapshot.dependencies
                for item in edge.evidence
            ),
        )


@dataclass(frozen=True, slots=True)
class _GraphValidationSession:
    source_ids: frozenset[str]
    facts: Mapping[str, ImportFact]
    support: frozenset[tuple[str, str, str, ResolutionKind]]

    def normalize(self, graph: ViewGraph) -> ViewGraph:
        if type(graph) is not ViewGraph:
            raise ValueError("a graph strategy must return ViewGraph")
        if not all(
            type(value) is tuple
            for value in (graph.nodes, graph.dependencies, graph.retained_fact_ids)
        ):
            raise ValueError("graph collections must be immutable tuples")
        source_ids = self.source_ids
        facts = self.facts
        support = self.support
        owner: dict[str, str] = {}
        nodes: dict[str, ViewNode] = {}
        for node in graph.nodes:
            if type(node) is not ViewNode:
                raise ValueError("graph nodes must be ViewNode values")
            if type(node.id) is not str or not node.id or node.id in nodes:
                raise ValueError("view node IDs must be nonempty and unique")
            if type(node.label) is not str or not node.label:
                raise ValueError("view node labels must be nonempty strings")
            if type(node.members) is not tuple or not node.members:
                raise ValueError("view nodes require nonempty immutable memberships")
            for member in node.members:
                if (
                    type(member) is not str
                    or member not in source_ids
                    or member in owner
                ):
                    raise ValueError(
                        "view memberships must be disjoint original sources"
                    )
                owner[member] = node.id
            members = (
                node.members if len(node.members) == 1 else tuple(sorted(node.members))
            )
            nodes[node.id] = (
                node
                if members == node.members
                else ViewNode(node.id, node.label, members)
            )
        if not all(type(fact_id) is str for fact_id in graph.retained_fact_ids):
            raise ValueError("retained fact IDs must be strings")
        retained = set(graph.retained_fact_ids)
        if (
            len(retained) != len(graph.retained_fact_ids)
            or not retained <= facts.keys()
        ):
            raise ValueError("retained fact IDs must be unique original facts")
        if any(facts[fact_id].source not in owner for fact_id in retained):
            raise ValueError("retained facts must belong to a projected source")
        edges: dict[tuple[str, str], ViewEdge] = {}
        for edge in graph.dependencies:
            if type(edge) is not ViewEdge:
                raise ValueError("graph dependencies must be ViewEdge values")
            if type(edge.source) is not str or type(edge.target) is not str:
                raise ValueError("view edge endpoints must be string node IDs")
            pair = (edge.source, edge.target)
            if edge.source not in nodes or edge.target not in nodes or pair in edges:
                raise ValueError("view edges require unique pairs of existing nodes")
            if type(edge.evidence) is not tuple or not edge.evidence:
                raise ValueError("view edges require nonempty immutable evidence")
            for item in edge.evidence:
                if (
                    type(item) is not ViewEvidence
                    or type(item.resolution_kind) is not ResolutionKind
                ):
                    raise ValueError("edge evidence must be ViewEvidence values")
                if (
                    type(item.source) is not str
                    or type(item.target) is not str
                    or type(item.fact_id) is not str
                ):
                    raise ValueError("view evidence references must be string IDs")
                original = (
                    item.source,
                    item.target,
                    item.fact_id,
                    item.resolution_kind,
                )
                if original not in support or item.fact_id not in retained:
                    raise ValueError(
                        "view evidence is not supported by retained dependencies"
                    )
                if (owner.get(item.source), owner.get(item.target)) != pair:
                    raise ValueError(
                        "view evidence endpoints disagree with node memberships"
                    )
                if facts[item.fact_id].source != item.source:
                    raise ValueError("view evidence fact does not belong to its source")
            evidence = (
                edge.evidence
                if len(edge.evidence) == 1
                else tuple(sorted(set(edge.evidence), key=self._evidence_key))
            )
            edges[pair] = (
                edge
                if evidence == edge.evidence
                else ViewEdge(edge.source, edge.target, evidence)
            )
        normalized_nodes = tuple(nodes[key] for key in sorted(nodes))
        normalized_edges = tuple(edges[key] for key in sorted(edges))
        normalized_retained = tuple(sorted(retained))
        if (
            normalized_nodes == graph.nodes
            and normalized_edges == graph.dependencies
            and normalized_retained == graph.retained_fact_ids
        ):
            return graph
        return ViewGraph(normalized_nodes, normalized_edges, normalized_retained)

    @staticmethod
    def _evidence_key(item: ViewEvidence) -> tuple[str, str, str, str]:
        return item.source, item.target, item.fact_id, item.resolution_kind.value
