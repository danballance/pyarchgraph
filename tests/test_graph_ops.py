from __future__ import annotations

import hashlib

import pytest

from pyarchgraph import graph_ops
from pyarchgraph.graph_ops import build_dag
from pyarchgraph.model import DependencyEdge, SourceModule


def _module(module_id: str) -> SourceModule:
    return SourceModule(
        id=module_id,
        path=f"{module_id.replace('.', '/')}.py",
        is_package=False,
        parent_package=module_id.rpartition(".")[0] or None,
    )


def _dependency(source: str, target: str) -> DependencyEdge:
    return DependencyEdge(source=source, target=target, evidence=())


def _component_id(*members: str) -> str:
    digest = hashlib.sha256("\0".join(sorted(members)).encode()).hexdigest()
    return f"scc-{digest[:12]}"


def test_chain_has_dependency_first_layers_and_keeps_isolated_module() -> None:
    dag = build_dag(
        tuple(_module(name) for name in ("isolated", "c", "a", "b")),
        (_dependency("b", "c"), _dependency("a", "b")),
    )

    assert [node.members for node in dag.nodes] == [
        ("a",),
        ("b",),
        ("c",),
        ("isolated",),
    ]
    assert all(not node.cyclic for node in dag.nodes)
    assert [(edge.source, edge.target) for edge in dag.edges] == sorted(
        [
            (_component_id("a"), _component_id("b")),
            (_component_id("b"), _component_id("c")),
        ]
    )
    assert dag.dependency_first_layers == (
        tuple(sorted((_component_id("c"), _component_id("isolated")))),
        (_component_id("b"),),
        (_component_id("a"),),
    )


def test_cycle_is_contracted_and_cross_component_raw_edges_are_retained() -> None:
    dag = build_dag(
        tuple(_module(name) for name in ("c", "b", "a")),
        (
            _dependency("a", "b"),
            _dependency("b", "a"),
            _dependency("b", "c"),
            _dependency("a", "c"),
            # A duplicate domain edge cannot duplicate the raw reference.
            _dependency("a", "c"),
        ),
    )

    assert [(node.members, node.cyclic) for node in dag.nodes] == [
        (("a", "b"), True),
        (("c",), False),
    ]
    assert len(dag.edges) == 1
    assert (dag.edges[0].source, dag.edges[0].target) == (
        _component_id("a", "b"),
        _component_id("c"),
    )
    assert [(raw.source, raw.target) for raw in dag.edges[0].raw_dependencies] == [
        ("a", "c"),
        ("b", "c"),
    ]
    assert dag.dependency_first_layers == (
        (_component_id("c"),),
        (_component_id("a", "b"),),
    )


def test_singleton_self_loop_is_cyclic_but_creates_no_dag_edge() -> None:
    dag = build_dag((_module("selfish"),), (_dependency("selfish", "selfish"),))

    assert len(dag.nodes) == 1
    assert dag.nodes[0].members == ("selfish",)
    assert dag.nodes[0].cyclic is True
    assert dag.edges == ()
    assert dag.dependency_first_layers == ((_component_id("selfish"),),)


def test_component_prefix_collisions_are_extended_deterministically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digests = {
        ("a",): "123456789abc0" + "0" * 51,
        ("b",): "123456789abc1" + "1" * 51,
        ("c",): "fedcba9876542" + "2" * 51,
    }
    monkeypatch.setattr(graph_ops, "_component_digest", digests.__getitem__)

    forward = graph_ops._stable_component_ids((("a",), ("b",), ("c",)))
    reverse = graph_ops._stable_component_ids((("c",), ("b",), ("a",)))

    assert forward == reverse
    assert forward[("a",)] == "scc-123456789abc0"
    assert forward[("b",)] == "scc-123456789abc1"
    assert forward[("c",)] == "scc-fedcba987654"


def test_build_dag_rejects_edges_outside_the_module_inventory() -> None:
    with pytest.raises(ValueError, match="indexed modules"):
        build_dag((_module("a"),), (_dependency("a", "missing"),))
