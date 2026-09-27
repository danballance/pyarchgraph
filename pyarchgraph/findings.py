"""Check the structural import graph and explain each blocking concern."""

from collections.abc import Callable
from dataclasses import replace

import networkx as nx

from pyarchgraph.model import (
    CycleFinding,
    DependencyEdge,
    Details,
    EvidenceLocation,
    Finding,
    FindingDependency,
    GraphView,
    GraphViews,
    ImportFact,
    ImportFinding,
    ResolutionKind,
    UnresolvedImport,
    UnresolvedReason,
)

DEFINITE_KINDS = frozenset({ResolutionKind.EXACT_MODULE, ResolutionKind.EXACT_BASE})


def _cyclic_components(graph: nx.DiGraph) -> list[tuple[str, ...]]:
    return sorted(
        tuple(sorted(members))
        for members in nx.strongly_connected_components(graph)
        if len(members) > 1 or graph.has_edge(next(iter(members)), next(iter(members)))
    )


def _location(fact: ImportFact, kind: ResolutionKind | None = None) -> EvidenceLocation:
    return EvidenceLocation(
        fact.path, fact.line, fact.column + 1, fact.source_segment, kind, fact.context
    )


def _unique_locations(
    evidence: tuple[EvidenceLocation, ...],
) -> tuple[EvidenceLocation, ...]:
    """Collapse equal public records, without merging separate source sites."""

    return tuple(
        sorted(
            set(evidence),
            key=lambda item: (
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
            ),
        )
    )


def build_findings(
    dependencies: tuple[DependencyEdge, ...],
    facts: tuple[ImportFact, ...],
    unresolved_imports: tuple[UnresolvedImport, ...],
    *,
    details: Details = "summary",
) -> tuple[Finding, ...]:
    """Build two graphs once, with one bounded witness per cyclic component.

    Probable edges matter only when they participate in a cycle. Missing
    targets independently identify gaps in explicit import resolution.
    """

    if details not in ("summary", "component-edges"):
        raise ValueError("details must be 'summary' or 'component-edges'")
    facts_by_id = {fact.id: fact for fact in facts}
    by_pair = {(edge.source, edge.target): edge for edge in dependencies}
    graph = nx.DiGraph()
    graph.add_edges_from(sorted(by_pair))
    definite = nx.DiGraph()
    definite.add_edges_from(
        pair
        for pair in sorted(by_pair)
        if any(
            item.resolution_kind in DEFINITE_KINDS for item in by_pair[pair].evidence
        )
    )
    definite_cyclic_members = {
        member for group in _cyclic_components(definite) for member in group
    }

    def dependency(pair: tuple[str, str], *, definite_only: bool) -> FindingDependency:
        edge = by_pair[pair]
        return FindingDependency(
            edge.source,
            edge.target,
            _unique_locations(
                tuple(
                    _location(facts_by_id[item.fact_id], item.resolution_kind)
                    for item in edge.evidence
                    if not definite_only or item.resolution_kind in DEFINITE_KINDS
                )
            ),
        )

    findings: list[Finding] = []
    components = _cyclic_components(graph)
    component_by_member = {
        member: index for index, members in enumerate(components) for member in members
    }
    component_pairs: list[list[tuple[str, str]]] = [[] for _ in components]
    for source, target in sorted(by_pair):
        index = component_by_member.get(source)
        if index is not None and component_by_member.get(target) == index:
            component_pairs[index].append((source, target))
    for index, members in enumerate(components):
        definite_members = tuple(sorted(set(members) & definite_cyclic_members))
        witness_graph = (
            definite.subgraph(members) if definite_members else graph.subgraph(members)
        )
        start = definite_members[0] if definite_members else members[0]
        findings.append(
            CycleFinding(
                certainty="definite" if definite_members else "possible",
                members=members,
                definite_members=definite_members,
                witness=tuple(
                    dependency((source, target), definite_only=bool(definite_members))
                    for source, target in nx.find_cycle(witness_graph, source=start)
                ),
                dependency_count=len(component_pairs[index]),
                dependencies=(
                    tuple(
                        dependency(pair, definite_only=False)
                        for pair in component_pairs[index]
                    )
                    if details == "component-edges"
                    else None
                ),
            )
        )
    for unresolved in unresolved_imports:
        if unresolved.reason is UnresolvedReason.NAMESPACE_BASE_UNMODELLED:
            continue
        evidence = sorted(
            (facts_by_id[fact_id] for fact_id in unresolved.fact_ids),
            key=lambda fact: (fact.path, fact.line, fact.column, fact.alias_index),
        )
        messages = {
            UnresolvedReason.MISSING_INTERNAL_TARGET: f"Internal import target {unresolved.requested!r} was not found.",
            UnresolvedReason.RELATIVE_ESCAPE: f"Relative import {unresolved.requested!r} escapes its package.",
            UnresolvedReason.UNKNOWN_PACKAGE_CONTEXT: f"Relative import {unresolved.requested!r} has no known package context.",
            UnresolvedReason.AMBIGUOUS_TARGET: f"Import target {unresolved.requested!r} has ambiguous source bindings.",
        }
        findings.append(
            ImportFinding(
                kind="unresolved_import",
                source=unresolved.source,
                requested=unresolved.requested,
                code=unresolved.reason.value,
                message=messages[unresolved.reason],
                evidence=_unique_locations(tuple(_location(fact) for fact in evidence)),
            )
        )

    return tuple(findings)


def build_views(
    dependencies: tuple[DependencyEdge, ...],
    facts: tuple[ImportFact, ...],
    unresolved_imports: tuple[UnresolvedImport, ...],
    *,
    details: Details = "summary",
) -> GraphViews:
    """Recompute three graphs after filtering their supporting import facts.

    Input dependencies have already undergone architecture edge selection.
    Resolution certainty is determined from surviving evidence, independently
    of typing guards or function scope. These are syntactic, not runtime views.
    """

    def view(eligible: Callable[[ImportFact], bool]) -> GraphView:
        fact_ids = {fact.id for fact in facts if eligible(fact)}
        selected_edges = tuple(
            replace(edge, evidence=evidence)
            for edge in dependencies
            if (
                evidence := tuple(
                    item for item in edge.evidence if item.fact_id in fact_ids
                )
            )
        )
        selected_unresolved = tuple(
            replace(item, fact_ids=retained)
            for item in unresolved_imports
            if (
                retained := tuple(
                    fact_id for fact_id in item.fact_ids if fact_id in fact_ids
                )
            )
        )
        findings = build_findings(
            selected_edges, facts, selected_unresolved, details=details
        )
        cycles = tuple(item for item in findings if isinstance(item, CycleFinding))
        return GraphView(
            dependency_count=len(selected_edges),
            cyclic_source_count=sum(len(item.members) for item in cycles),
            cyclic_dependency_count=sum(item.dependency_count for item in cycles),
            findings=findings,
        )

    return GraphViews(
        structural=view(lambda fact: True),
        non_typing=view(lambda fact: not fact.context.typing_only),
        module_body=view(
            lambda fact: not fact.context.typing_only and not fact.context.in_function
        ),
    )
