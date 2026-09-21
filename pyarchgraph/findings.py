"""Bounded, deterministic findings with import evidence and semantic identity."""

from dataclasses import asdict
from fnmatch import fnmatchcase
import hashlib
import json

import networkx as nx

from pyarchgraph.model import DependencyEdge, ImportFact
from pyarchgraph.policy import DEFINITE_KINDS


def semantic_id(kind: str, value: object) -> str:
    encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
    return f"{kind}-{hashlib.sha256(encoded).hexdigest()}"


def _cyclic_components(graph: nx.DiGraph) -> list[tuple[str, ...]]:
    return sorted(
        tuple(sorted(members))
        for members in nx.strongly_connected_components(graph)
        if len(members) > 1 or graph.has_edge(next(iter(members)), next(iter(members)))
    )


def dependency_record(edge: DependencyEdge, facts: dict[str, ImportFact]) -> dict:
    return {
        "id": semantic_id("dependency", (edge.source, edge.target)),
        "source": edge.source,
        "target": edge.target,
        "evidence": [
            {
                **asdict(facts[item.fact_id]),
                "resolution_kind": item.resolution_kind.value,
            }
            for item in edge.evidence
        ],
    }


def build_findings(
    dependencies: tuple[DependencyEdge, ...],
    facts: tuple[ImportFact, ...],
    forbidden_dependencies: tuple[tuple[str, str], ...] = (),
) -> tuple[dict, ...]:
    """One cycle witness per full cyclic SCC; never enumerate simple cycles.

    A definite witness takes precedence if the component contains one. Members
    that depend on uncertain edges are reported separately from definite cyclic
    members. The witness is bounded by the component's node count.
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
    definite_components = _cyclic_components(definite)
    definite_cyclic_members = {
        member for group in definite_components for member in group
    }
    definite_component_for = {
        member: index
        for index, group in enumerate(definite_components)
        for member in group
    }
    findings = []
    for members in _cyclic_components(graph):
        member_set = set(members)
        definite_members = sorted(member_set & definite_cyclic_members)
        certainty = "definite" if definite_members else "possible"
        witness_graph = (
            definite.subgraph(members) if definite_members else graph.subgraph(members)
        )
        start = definite_members[0] if definite_members else members[0]
        witness_pairs = nx.find_cycle(witness_graph, source=start)
        witness = []
        for source, target in witness_pairs:
            edge = by_pair[(source, target)]
            if definite_members:
                edge = DependencyEdge(
                    source,
                    target,
                    tuple(
                        item
                        for item in edge.evidence
                        if item.resolution_kind in DEFINITE_KINDS
                    ),
                )
            witness.append(dependency_record(edge, facts_by_id))
        internal_pairs = sorted(
            (source, target)
            for source in members
            for target in graph.successors(source)
            if target in member_set
        )
        definite_pairs = [
            pair
            for pair in internal_pairs
            if pair[0] in definite_component_for
            and definite_component_for.get(pair[1]) == definite_component_for[pair[0]]
            and definite.has_edge(*pair)
        ]
        findings.append(
            {
                "id": semantic_id("cycle", members),
                "kind": "cycle",
                "certainty": certainty,
                "members": list(members),
                "definite_members": definite_members,
                "cyclic_dependencies": [list(pair) for pair in internal_pairs],
                "definite_cyclic_dependencies": [list(pair) for pair in definite_pairs],
                "witness": witness,
            }
        )
    for pair, edge in sorted(by_pair.items()):
        matched = [
            list(rule)
            for rule in forbidden_dependencies
            if fnmatchcase(pair[0], rule[0]) and fnmatchcase(pair[1], rule[1])
        ]
        if matched:
            findings.append(
                {
                    "id": semantic_id("forbidden", pair),
                    "kind": "forbidden_dependency",
                    "certainty": "definite" if definite.has_edge(*pair) else "possible",
                    "rules": matched,
                    "witness": [dependency_record(edge, facts_by_id)],
                }
            )
    return tuple(findings)


def check_status(result) -> str:
    """Three-valued policy result; a high heuristic score never clears a cycle."""
    if any(finding["certainty"] == "definite" for finding in result.findings):
        return "fail"
    if (
        not result.complete
        or not result.scope_valid
        or not result.modules
        or not result.dependency_resolution_complete
        or result.findings
    ):
        return "needs_review"
    return "pass"
