from __future__ import annotations

import pytest

from pyarchgraph.graph_ops import build_dag, essential_edges
from pyarchgraph.model import DependencyEdge, SourceModule
from pyarchgraph.projection import build_package_dag, package_of


def _module(module_id: str) -> SourceModule:
    return SourceModule(
        id=module_id,
        path=module_id.replace(".", "/") + ".py",
        is_package=False,
        parent_package=module_id.rsplit(".", 1)[0] if "." in module_id else None,
    )


def _edge(source: str, target: str) -> DependencyEdge:
    return DependencyEdge(source=source, target=target, evidence=())


def test_package_of_truncates_to_the_requested_depth() -> None:
    assert package_of("app.kernel.ports.store", 2) == "app.kernel"
    assert package_of("app.kernel.ports.store", 1) == "app"


def test_package_of_keeps_a_module_shallower_than_the_depth() -> None:
    """Every module must map to a node, or the projection would drop it."""
    assert package_of("app", 2) == "app"
    assert package_of("app.cli", 3) == "app.cli"


def test_package_of_rejects_a_depth_below_one() -> None:
    with pytest.raises(ValueError, match="package depth"):
        package_of("app.cli", 0)


def test_projection_collapses_modules_sharing_a_package() -> None:
    modules = tuple(
        _module(name)
        for name in ("app.http.routes", "app.http.state", "app.kernel.models")
    )
    dependencies = (
        _edge("app.http.routes", "app.kernel.models"),
        _edge("app.http.state", "app.kernel.models"),
    )

    dag = build_package_dag(modules, dependencies, 2)

    assert [node.members for node in dag.nodes] == [("app.http",), ("app.kernel",)]
    members_of = {node.id: node.members[0] for node in dag.nodes}
    assert [
        (members_of[edge.source], members_of[edge.target]) for edge in dag.edges
    ] == [("app.http", "app.kernel")]


def test_projected_edge_carries_the_module_imports_behind_it() -> None:
    """The count on a package edge must mean something a reader can check."""
    modules = tuple(
        _module(name)
        for name in ("app.http.routes", "app.http.state", "app.kernel.models")
    )
    dependencies = (
        _edge("app.http.routes", "app.kernel.models"),
        _edge("app.http.state", "app.kernel.models"),
    )

    dag = build_package_dag(modules, dependencies, 2)

    assert [(raw.source, raw.target) for raw in dag.edges[0].raw_dependencies] == [
        ("app.http.routes", "app.kernel.models"),
        ("app.http.state", "app.kernel.models"),
    ]


def test_intra_package_imports_do_not_become_self_edges() -> None:
    modules = tuple(_module(name) for name in ("app.http.routes", "app.http.state"))
    dependencies = (_edge("app.http.routes", "app.http.state"),)

    dag = build_package_dag(modules, dependencies, 2)

    assert [node.members for node in dag.nodes] == [("app.http",)]
    assert dag.edges == ()
    assert not dag.nodes[0].cyclic


def test_projection_condenses_a_cycle_that_grouping_creates() -> None:
    """Grouping can make a cycle the module graph does not have.

    ``a.one -> b.one -> a.two`` is acyclic per module but ``a <-> b`` once
    projected, so the projection must go through the same SCC condensation
    rather than assume the module graph's acyclicity survives.
    """
    modules = tuple(_module(name) for name in ("a.one", "a.two", "b.one"))
    dependencies = (_edge("a.one", "b.one"), _edge("b.one", "a.two"))

    module_dag = build_dag(modules, dependencies)
    assert not any(node.cyclic for node in module_dag.nodes)

    package_dag = build_package_dag(modules, dependencies, 1)

    assert [node.members for node in package_dag.nodes] == [("a", "b")]
    assert package_dag.nodes[0].cyclic
    assert package_dag.edges == ()


def test_essential_edges_drops_only_edges_implied_by_a_longer_path() -> None:
    modules = tuple(_module(name) for name in ("a", "b", "c"))
    dependencies = (_edge("a", "b"), _edge("b", "c"), _edge("a", "c"))

    dag = build_dag(modules, dependencies)
    by_member = {node.members[0]: node.id for node in dag.nodes}
    essential = essential_edges(dag)

    assert len(dag.edges) == 3
    assert essential == {
        (by_member["a"], by_member["b"]),
        (by_member["b"], by_member["c"]),
    }


def test_essential_edges_keeps_a_graph_with_no_redundancy_intact() -> None:
    modules = tuple(_module(name) for name in ("a", "b", "c"))
    dependencies = (_edge("a", "b"), _edge("a", "c"))

    dag = build_dag(modules, dependencies)

    assert len(essential_edges(dag)) == len(dag.edges)
