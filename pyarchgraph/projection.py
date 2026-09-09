"""Project a module graph onto a package prefix before condensation.

A module-level DAG of a well-layered system is legible only in the small: a
shared foundation module is depended on directly from every layer above it, so
most edges span most of the drawing. Grouping modules by package prefix keeps
the same reachability story at a grain a reader can hold.

The projection is a presentation of the evidence, never a replacement for it.
An ``AnalysisResult`` still carries every module, import fact and module-level
dependency; only its condensation DAG is projected.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from pyarchgraph.graph_ops import build_dag
from pyarchgraph.model import (
    Dag,
    DagEdge,
    DependencyEdge,
    RawDependency,
    SourceModule,
)

MINIMUM_PACKAGE_DEPTH = 1


def package_of(module_id: str, depth: int) -> str:
    """Return the ancestor of ``module_id`` at ``depth`` dotted segments.

    A module shallower than ``depth`` is its own package, so every module maps
    to exactly one node and no module is dropped.
    """

    if depth < MINIMUM_PACKAGE_DEPTH:
        raise ValueError(f"package depth must be >= {MINIMUM_PACKAGE_DEPTH}")
    return ".".join(module_id.split(".")[:depth])


def build_package_dag(
    modules: Iterable[SourceModule],
    dependencies: Iterable[DependencyEdge],
    depth: int,
) -> Dag:
    """Condense the package projection of a module graph.

    Delegates to :func:`build_dag` so a package-level cycle — which grouping
    can create even when no module-level cycle exists — is condensed by the
    same strongly-connected-component pass, rather than being assumed away.

    Each resulting edge carries the module-level pairs that back it, so the
    projection can still say how much weight sits behind a package edge.
    """

    module_list = tuple(modules)
    dependency_list = tuple(dependencies)

    package_ids = sorted({package_of(module.id, depth) for module in module_list})
    package_modules = tuple(
        SourceModule(
            id=package_id,
            path=package_id.replace(".", "/"),
            is_package=True,
            parent_package=(
                package_id.rsplit(".", 1)[0] if "." in package_id else None
            ),
        )
        for package_id in package_ids
    )

    backing: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    package_dependencies: list[DependencyEdge] = []
    seen: set[tuple[str, str]] = set()
    for dependency in dependency_list:
        source = package_of(dependency.source, depth)
        target = package_of(dependency.target, depth)
        backing[(source, target)].add((dependency.source, dependency.target))
        if source == target or (source, target) in seen:
            continue
        seen.add((source, target))
        package_dependencies.append(
            DependencyEdge(source=source, target=target, evidence=())
        )

    dag = build_dag(package_modules, tuple(package_dependencies))
    return Dag(
        nodes=dag.nodes,
        edges=tuple(
            DagEdge(
                source=edge.source,
                target=edge.target,
                raw_dependencies=_backing_pairs(edge, backing),
            )
            for edge in dag.edges
        ),
        dependency_first_layers=dag.dependency_first_layers,
    )


def _backing_pairs(
    edge: DagEdge,
    backing: dict[tuple[str, str], set[tuple[str, str]]],
) -> tuple[RawDependency, ...]:
    """Return the module-level imports behind one condensed package edge."""

    pairs: set[tuple[str, str]] = set()
    for package_pair in edge.raw_dependencies:
        pairs |= backing[(package_pair.source, package_pair.target)]
    return tuple(
        RawDependency(source=source, target=target) for source, target in sorted(pairs)
    )
