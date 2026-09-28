"""Immutable graph projections and analysis contexts."""

from dataclasses import dataclass

from pyarchgraph.domain.models import (
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
    """Represent one view node and the source files it groups."""

    id: str
    label: str
    members: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ViewEvidence:
    """Keep original source endpoints and their supporting fact visible in a view."""

    source: str
    target: str
    fact_id: str
    resolution_kind: ResolutionKind


@dataclass(frozen=True, slots=True)
class ViewEdge:
    """Connect two view nodes using evidence from original source dependencies."""

    source: str
    target: str
    evidence: tuple[ViewEvidence, ...]


@dataclass(frozen=True, slots=True)
class ViewGraph:
    """Hold the nodes, dependencies and import fact IDs retained by one view."""

    nodes: tuple[ViewNode, ...]
    dependencies: tuple[ViewEdge, ...]
    retained_fact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AnalysisSnapshot:
    """Provide the shared source facts and resolution results used to build views."""

    sources: tuple[SourceModule, ...]
    facts: tuple[ImportFact, ...]
    dependencies: tuple[DependencyEdge, ...]
    external_imports: tuple[ExternalImport, ...] = ()
    unresolved_imports: tuple[UnresolvedImport, ...] = ()


@dataclass(frozen=True, slots=True)
class CheckContext:
    """Provide a check with one view's graph, retained imports and cycle analysis."""

    graph: ViewGraph
    facts: tuple[ImportFact, ...]
    external_imports: tuple[ExternalImport, ...]
    unresolved_imports: tuple[UnresolvedImport, ...]
    sources: tuple[SourceModule, ...]
    cycle_analysis: tuple[CycleFinding, ...]
    details: Details = "summary"
