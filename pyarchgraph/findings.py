"""Check the structural import graph and explain each blocking concern."""

from fnmatch import fnmatchcase

import networkx as nx

from pyarchgraph.model import (
    CycleFinding,
    DependencyEdge,
    Diagnostic,
    EvidenceLocation,
    Finding,
    FindingDependency,
    ForbiddenDependencyFinding,
    ImportFact,
    ImportFinding,
    ImportSyntax,
    ResolutionKind,
    SourceModule,
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
        fact.path, fact.line, fact.column + 1, fact.source_segment, kind
    )


def build_findings(
    dependencies: tuple[DependencyEdge, ...],
    facts: tuple[ImportFact, ...],
    modules: tuple[SourceModule, ...],
    unresolved_imports: tuple[UnresolvedImport, ...],
    diagnostics: tuple[Diagnostic, ...],
    forbidden_dependencies: tuple[tuple[str, str], ...],
) -> tuple[Finding, ...]:
    """Build two graphs once, with one bounded witness per cyclic component.

    Probable edges matter only when they participate in a violation. Missing
    targets and dynamic calls independently identify gaps in static coverage.
    """

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
        evidence = sorted(
            (
                (facts_by_id[item.fact_id], item.resolution_kind)
                for item in edge.evidence
                if not definite_only or item.resolution_kind in DEFINITE_KINDS
            ),
            key=lambda item: (
                item[0].path,
                item[0].line,
                item[0].column,
                item[1].value,
            ),
        )
        return FindingDependency(
            edge.source,
            edge.target,
            tuple(_location(fact, kind) for fact, kind in evidence),
        )

    findings: list[Finding] = []
    for members in _cyclic_components(graph):
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
            )
        )
    for pair in sorted(by_pair):
        matched = tuple(
            rule
            for rule in forbidden_dependencies
            if fnmatchcase(pair[0], rule[0]) and fnmatchcase(pair[1], rule[1])
        )
        if matched:
            findings.append(
                ForbiddenDependencyFinding(
                    certainty="definite" if definite.has_edge(*pair) else "possible",
                    rules=matched,
                    witness=(dependency(pair, definite_only=False),),
                )
            )
    for unresolved in unresolved_imports:
        if unresolved.reason is UnresolvedReason.NAMESPACE_BASE_UNMODELLED:
            continue
        evidence = sorted(
            (facts_by_id[fact_id] for fact_id in unresolved.fact_ids),
            key=lambda fact: (fact.path, fact.line, fact.column, fact.alias_index),
        )
        message = (
            f"Internal import target {unresolved.requested!r} was not found."
            if unresolved.reason is UnresolvedReason.MISSING_INTERNAL_TARGET
            else f"Relative import {unresolved.requested!r} escapes its package."
        )
        findings.append(
            ImportFinding(
                kind="unresolved_import",
                source=unresolved.source,
                requested=unresolved.requested,
                code=unresolved.reason.value,
                message=message,
                evidence=tuple(_location(fact) for fact in evidence),
            )
        )

    modules_by_path = {module.path: module.id for module in modules}
    dynamic_facts = {
        (fact.path, fact.line, fact.column): fact
        for fact in facts
        if fact.syntax is ImportSyntax.DYNAMIC_IMPORT
    }
    for diagnostic in diagnostics:
        if diagnostic.code != "dynamic_import_ignored":
            continue
        # Recognized call diagnostics always carry their exact AST location.
        assert diagnostic.path is not None
        assert diagnostic.line is not None
        assert diagnostic.column is not None
        fact = dynamic_facts.get((diagnostic.path, diagnostic.line, diagnostic.column))
        findings.append(
            ImportFinding(
                kind="dynamic_import",
                source=modules_by_path[diagnostic.path],
                requested=fact.base_module if fact else None,
                code=diagnostic.code,
                message=diagnostic.message,
                evidence=(
                    _location(fact, ResolutionKind.DYNAMIC_LITERAL)
                    if fact
                    else EvidenceLocation(
                        diagnostic.path,
                        diagnostic.line,
                        diagnostic.column + 1,
                        diagnostic.source_segment,
                        None,
                    ),
                ),
            )
        )
    return tuple(findings)
