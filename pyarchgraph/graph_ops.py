"""Build the raw module graph's strongly-connected-component DAG.

The public result contains only frozen domain objects.  NetworkX node IDs are
deliberately kept inside this module because its condensation graph numbers
components according to an implementation detail rather than their contents.
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
from typing import Iterable

import networkx as nx

from pyarchgraph.model import (
    Dag,
    DagEdge,
    DagNode,
    DependencyEdge,
    RawDependency,
    SourceModule,
)


_COMPONENT_ID_PREFIX_LENGTH = 12
_COMPONENT_ID_NAMESPACE = "scc-"


def _component_digest(members: tuple[str, ...]) -> str:
    """Return the content digest from which a component ID is derived."""

    payload = "\0".join(members).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _stable_component_ids(
    components: Iterable[tuple[str, ...]],
) -> dict[tuple[str, ...], str]:
    """Assign the shortest unique component-digest prefixes of at least 12.

    All components which collide at a given prefix length are lengthened
    together.  This makes the result independent of traversal order.  A full
    SHA-256 collision cannot be resolved by prefix extension, so it is treated
    as an invariant failure instead of silently creating duplicate node IDs.
    """

    ordered_components = tuple(sorted(components))
    digests = {members: _component_digest(members) for members in ordered_components}
    prefix_lengths = {
        members: _COMPONENT_ID_PREFIX_LENGTH for members in ordered_components
    }

    while True:
        groups: dict[str, list[tuple[str, ...]]] = defaultdict(list)
        for members in ordered_components:
            digest = digests[members]
            prefix_length = prefix_lengths[members]
            groups[digest[:prefix_length]].append(members)

        collisions = tuple(group for group in groups.values() if len(group) > 1)
        if not collisions:
            break

        for group in collisions:
            for members in group:
                digest = digests[members]
                next_length = prefix_lengths[members] + 1
                if next_length > len(digest):
                    raise ValueError(
                        "distinct SCC member sets produced the same full digest"
                    )
                prefix_lengths[members] = next_length

    return {
        members: _COMPONENT_ID_NAMESPACE + digests[members][: prefix_lengths[members]]
        for members in ordered_components
    }


def build_dag(
    modules: tuple[SourceModule, ...],
    dependencies: tuple[DependencyEdge, ...],
) -> Dag:
    """Contract a raw syntactic-import graph into a deterministic DAG.

    Every source-backed module is added before any edge, so isolated modules
    remain visible.  A component is cyclic when it has multiple members or its
    sole member has a raw self-loop.  Layers are calculated over the reversed
    condensation graph because raw edges point from importer to dependency.
    """

    module_ids = tuple(module.id for module in modules)
    known_modules = set(module_ids)
    if len(known_modules) != len(module_ids):
        raise ValueError("module IDs must be unique when building the DAG")

    raw_graph = nx.DiGraph()
    raw_graph.add_nodes_from(sorted(known_modules))

    raw_pairs: set[tuple[str, str]] = set()
    for dependency in dependencies:
        if (
            dependency.source not in known_modules
            or dependency.target not in known_modules
        ):
            raise ValueError(
                "dependency endpoints must both be indexed modules: "
                f"{dependency.source!r} -> {dependency.target!r}"
            )
        raw_pairs.add((dependency.source, dependency.target))
    raw_graph.add_edges_from(sorted(raw_pairs))

    component_members = tuple(
        sorted(
            tuple(sorted(component))
            for component in nx.strongly_connected_components(raw_graph)
        )
    )
    component_ids = _stable_component_ids(component_members)

    # Supplying the already sorted SCCs prevents NetworkX traversal order from
    # influencing its incidental integer component IDs.
    condensed_graph = nx.condensation(
        raw_graph, (set(members) for members in component_members)
    )
    if not nx.is_directed_acyclic_graph(condensed_graph):
        raise AssertionError("the SCC condensation graph must be acyclic")

    integer_to_stable: dict[int, str] = {}
    for integer_id, attributes in condensed_graph.nodes(data=True):
        members = tuple(sorted(attributes["members"]))
        integer_to_stable[integer_id] = component_ids[members]

    nodes = tuple(
        DagNode(
            id=component_ids[members],
            members=members,
            cyclic=(len(members) > 1 or raw_graph.has_edge(members[0], members[0])),
        )
        for members in component_members
    )

    module_to_integer: dict[str, int] = condensed_graph.graph["mapping"]
    raw_dependencies_by_edge: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(
        set
    )
    for source, target in sorted(raw_pairs):
        source_component = module_to_integer[source]
        target_component = module_to_integer[target]
        if source_component == target_component:
            continue
        stable_edge = (
            integer_to_stable[source_component],
            integer_to_stable[target_component],
        )
        raw_dependencies_by_edge[stable_edge].add((source, target))

    expected_edges = {
        (integer_to_stable[source], integer_to_stable[target])
        for source, target in condensed_graph.edges
    }
    if set(raw_dependencies_by_edge) != expected_edges:
        raise AssertionError(
            "condensed edges do not match cross-component raw dependencies"
        )

    edges = tuple(
        DagEdge(
            source=source,
            target=target,
            raw_dependencies=tuple(
                RawDependency(source=raw_source, target=raw_target)
                for raw_source, raw_target in sorted(raw_dependencies)
            ),
        )
        for (source, target), raw_dependencies in sorted(
            raw_dependencies_by_edge.items()
        )
    )

    reversed_condensation = condensed_graph.reverse(copy=False)
    dependency_first_layers = tuple(
        tuple(sorted(integer_to_stable[integer_id] for integer_id in generation))
        for generation in nx.topological_generations(reversed_condensation)
    )

    return Dag(
        nodes=nodes,
        edges=edges,
        dependency_first_layers=dependency_first_layers,
    )


def essential_edges(dag: Dag) -> frozenset[tuple[str, str]]:
    """Return the transitive reduction of ``dag`` as source/target ID pairs.

    The reduction is the unique smallest edge set with the same reachability,
    so dropping the rest changes nothing a reader can conclude about what
    depends on what — it only removes edges implied by a longer path. On a
    layered codebase this is typically a large fraction of the drawing,
    because a foundation module is imported both directly and through every
    layer between.

    Intended for rendering only. The edges omitted here are real imports and
    remain in the analysis model.
    """

    condensed = nx.DiGraph()
    condensed.add_nodes_from(sorted(node.id for node in dag.nodes))
    condensed.add_edges_from(sorted((edge.source, edge.target) for edge in dag.edges))
    reduced = nx.transitive_reduction(condensed)
    return frozenset((source, target) for source, target in reduced.edges)
