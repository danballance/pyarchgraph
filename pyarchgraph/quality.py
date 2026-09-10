"""Score observed module dependencies without using the presentation DAG."""

from __future__ import annotations

from fractions import Fraction
from typing import Literal

import networkx as nx

from pyarchgraph.model import (
    ArchitectureMetrics,
    ArchitectureQuality,
    DependencyEdge,
    SourceModule,
)


FORMULA_VERSION = "architecture-v1"
CYCLE_WEIGHT = Fraction(70, 100)
REACH_WEIGHT = Fraction(30, 100)


def _reachable_pair_count(graph: nx.DiGraph, components: list[set[str]]) -> int:
    """Count distinct reachable pairs, excluding each module's own identity.

    One integer bitset per SCC contains its members and all reachable modules.
    Union successors in reverse topological order, so shared descendants are
    counted once. Multiply by the SCC's size because every member has the same
    reachability, excluding itself. This avoids a quadratic set of Python edge
    objects, although worst-case bit storage remains quadratic in module count.
    """

    condensed = nx.condensation(graph, components)
    positions = {module: index for index, module in enumerate(sorted(graph))}
    reachable: dict[int, int] = {}
    pair_count = 0
    for component in reversed(list(nx.topological_sort(condensed))):
        members = condensed.nodes[component]["members"]
        mask = 0
        for module in members:
            mask |= 1 << positions[module]
        for dependency in condensed.successors(component):
            mask |= reachable[dependency]
        reachable[component] = mask
        pair_count += len(members) * (mask.bit_count() - 1)
    return pair_count


def calculate_quality(
    modules: tuple[SourceModule, ...],
    dependencies: tuple[DependencyEdge, ...],
    *,
    complete: bool,
    unresolved_import_count: int = 0,
    dynamic_import_warning_count: int = 0,
) -> ArchitectureQuality:
    """Apply the fixed 70% cycle-avoidance / 30% isolation heuristic.

    Every resolved module edge has equal weight, regardless of how many import
    statements support it or whether those statements are local or type-only.
    Disconnected modules remain in the inventory but cannot dilute the score.
    No numeric score is claimed for an empty or incomplete source inventory.
    """

    module_ids = {module.id for module in modules}
    if len(module_ids) != len(modules):
        raise ValueError("module IDs must be unique when calculating quality")
    pairs = {(edge.source, edge.target) for edge in dependencies}
    if any(
        source not in module_ids or target not in module_ids for source, target in pairs
    ):
        raise ValueError("quality dependency endpoints must both be indexed modules")

    # Only endpoints participate in scoring. All other modules are isolated.
    graph = nx.DiGraph()
    graph.add_edges_from(sorted(pairs))
    components = list(nx.strongly_connected_components(graph))
    cyclic_components = [
        members
        for members in components
        if len(members) > 1 or graph.has_edge(next(iter(members)), next(iter(members)))
    ]
    cyclic_count = sum(len(members) for members in cyclic_components)
    active_count = len(graph)
    reachable_count = _reachable_pair_count(graph, components)
    # Keep the rational formula exact until each public numeric value is
    # converted once to a float, avoiding cancellation noise in saved scores.
    cycle_fraction = (
        Fraction(cyclic_count, active_count) if active_count else Fraction(0)
    )
    reach_fraction = (
        Fraction(reachable_count, active_count * (active_count - 1))
        if active_count > 1
        else Fraction(0)
    )
    metrics = ArchitectureMetrics(
        module_count=len(modules),
        active_module_count=active_count,
        isolated_module_count=len(modules) - active_count,
        dependency_count=len(pairs),
        cyclic_component_count=len(cyclic_components),
        cyclic_module_count=cyclic_count,
        largest_cycle_size=max(
            (len(members) for members in cyclic_components), default=0
        ),
        reachable_pair_count=reachable_count,
        max_fan_in=max((degree for _, degree in graph.in_degree()), default=0),
        max_fan_out=max((degree for _, degree in graph.out_degree()), default=0),
        cycle_fraction=float(cycle_fraction),
        reach_fraction=float(reach_fraction),
    )
    reason: Literal["incomplete_analysis", "no_modules"] | None = None
    if not complete:
        reason = "incomplete_analysis"
    elif not modules:
        reason = "no_modules"
    cycle_score = float(100 * (1 - cycle_fraction)) if reason is None else None
    isolation_score = float(100 * (1 - reach_fraction)) if reason is None else None
    score = (
        float(
            100
            * (
                CYCLE_WEIGHT * (1 - cycle_fraction)
                + REACH_WEIGHT * (1 - reach_fraction)
            )
        )
        if reason is None
        else None
    )
    return ArchitectureQuality(
        formula_version=FORMULA_VERSION,
        score=score,
        cycle_avoidance_score=cycle_score,
        dependency_isolation_score=isolation_score,
        unavailable_reason=reason,
        metrics=metrics,
        unresolved_import_count=unresolved_import_count,
        dynamic_import_warning_count=dynamic_import_warning_count,
    )
