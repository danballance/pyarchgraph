"""Absolute, evidence-backed dependency debt for architecture cleanup.

This module consumes the canonical graph model, not its saved scores or finding
counts. It is an internal JSON boundary helper, not a separate Python API.
"""

from __future__ import annotations

from copy import deepcopy
from fnmatch import fnmatchcase

import networkx as nx

from pyarchgraph.findings import semantic_id
from pyarchgraph.policy import DEFINITE_KINDS


CLEANUP_MODEL_VERSION = "policy-debt-v1"


def _components(graph: nx.DiGraph) -> list[tuple[str, ...]]:
    return sorted(
        tuple(sorted(members))
        for members in nx.strongly_connected_components(graph)
        if len(members) > 1 or graph.has_edge(next(iter(members)), next(iter(members)))
    )


def _cyclic_pairs(
    graph: nx.DiGraph, components: list[tuple[str, ...]]
) -> set[tuple[str, str]]:
    component_for = {
        member: index for index, members in enumerate(components) for member in members
    }
    return {
        (source, target)
        for source, target in graph.edges
        if source in component_for
        and component_for.get(target) == component_for[source]
    }


def build_cleanup_report(document: dict) -> dict:
    """Build deterministic debt from a trusted or independently validated graph.

    A typed directed dependency is one obligation. SCCs group work, but their
    count never determines debt. Uncertainty is preserved separately from the
    definite count; neither a low count nor missing observations establish that
    cleanup is complete.
    """

    analysis = document["analysis"]
    provenance = analysis.get("provenance") or {}
    rules = sorted(
        {tuple(rule) for rule in provenance.get("forbidden_dependencies", [])}
    )
    facts = {fact["id"]: fact for fact in document["import_facts"]}
    edges = {
        (edge["source"], edge["target"]): edge
        for edge in document["architecture_dependencies"]
    }
    definite_pairs = {
        pair
        for pair, edge in edges.items()
        if any(proof["resolution_kind"] in DEFINITE_KINDS for proof in edge["evidence"])
    }
    graph = nx.DiGraph()
    graph.add_edges_from(sorted(edges))
    definite = nx.DiGraph()
    definite.add_edges_from(sorted(definite_pairs))
    definite_components = _components(definite)
    definite_cycles = _cyclic_pairs(definite, definite_components)
    all_cycles = _cyclic_pairs(graph, _components(graph))

    violations = []

    def record(kind: str, pair: tuple[str, str], certain: bool, matched=()) -> dict:
        edge = edges[pair]
        # Keep all observations, including uncertain supporting evidence. The
        # record's certainty describes the relationship, not each import site.
        evidence = [
            {
                **deepcopy(facts[proof["fact_id"]]),
                "resolution_kind": proof["resolution_kind"],
            }
            for proof in sorted(
                edge["evidence"],
                key=lambda item: (item["fact_id"], item["resolution_kind"]),
            )
        ]
        return {
            "id": semantic_id(kind, pair),
            "kind": kind,
            "source": pair[0],
            "target": pair[1],
            "certainty": "definite" if certain else "possible",
            "rules": [list(rule) for rule in matched],
            "evidence": evidence,
        }

    for pair in sorted(all_cycles):
        violations.append(record("cyclic_dependency", pair, pair in definite_cycles))
    for pair in sorted(edges):
        matched = [
            rule
            for rule in rules
            if fnmatchcase(pair[0], rule[0]) and fnmatchcase(pair[1], rule[1])
        ]
        if matched:
            violations.append(
                record("forbidden_dependency", pair, pair in definite_pairs, matched)
            )
    violations.sort(
        key=lambda item: (
            item["certainty"],
            item["kind"],
            item["source"],
            item["target"],
        )
    )
    counts = {
        kind: sum(
            item["kind"] == kind and item["certainty"] == "definite"
            for item in violations
        )
        for kind in ("cyclic_dependency", "forbidden_dependency")
    }
    possible_ids = [
        item["id"] for item in violations if item["certainty"] == "possible"
    ]

    # Index once instead of scanning all violations for every disconnected SCC.
    cycle_by_source: dict[str, list[dict]] = {}
    for item in violations:
        if item["kind"] == "cyclic_dependency" and item["certainty"] == "definite":
            cycle_by_source.setdefault(item["source"], []).append(item)
    work_items = []
    for members in definite_components:
        items = [item for member in members for item in cycle_by_source[member]]
        witness = nx.find_cycle(definite.subgraph(members), source=members[0])
        work_items.append(
            {
                "id": semantic_id("cleanup_cycle", members),
                "kind": "cycle",
                "modules": list(members),
                "violation_count": len(items),
                "violation_ids": sorted(item["id"] for item in items),
                "witness": [semantic_id("cyclic_dependency", pair) for pair in witness],
            }
        )
    for item in violations:
        if item["kind"] == "forbidden_dependency" and item["certainty"] == "definite":
            work_items.append(
                {
                    "id": semantic_id(
                        "cleanup_forbidden", (item["source"], item["target"])
                    ),
                    "kind": "forbidden_dependency",
                    "modules": sorted({item["source"], item["target"]}),
                    "violation_count": 1,
                    "violation_ids": [item["id"]],
                    "witness": [item["id"]],
                }
            )
    work_items.sort(key=lambda item: (-item["violation_count"], item["id"]))

    coverage = {
        key: analysis[key]
        for key in ("complete", "scope_valid", "dependency_resolution_complete")
    }
    coverage.update(
        nonempty=bool(document["modules"]),
        limitations=deepcopy(document.get("limitations", [])),
        diagnostics=deepcopy(document.get("diagnostics", [])),
        unresolved_imports=deepcopy(document.get("unresolved_imports", [])),
    )
    ready = all(
        coverage[key]
        for key in (
            "complete",
            "scope_valid",
            "dependency_resolution_complete",
            "nonempty",
        )
    )
    return {
        "model_version": CLEANUP_MODEL_VERSION,
        "violation_count": sum(counts.values()),
        "counts": counts,
        "possible_violation_count": len(possible_ids),
        "possible_violation_ids": possible_ids,
        "cleanup_complete": ready
        and not violations
        and document["check"]["status"] == "pass",
        "coverage": coverage,
        "violations": violations,
        "work_items": work_items,
    }
