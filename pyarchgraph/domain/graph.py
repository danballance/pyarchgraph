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
