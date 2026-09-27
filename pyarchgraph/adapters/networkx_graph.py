"""NetworkX implementation of the graph-algorithm port."""

import networkx as nx

from pyarchgraph.domain.graph_algorithms import GraphAlgorithms, GraphHandle


class NetworkXGraphHandle(GraphHandle):
    def __init__(
        self, nodes: tuple[str, ...], edges: tuple[tuple[str, str], ...]
    ) -> None:
        self._graph = nx.DiGraph()
        self._graph.add_nodes_from(nodes)
        self._graph.add_edges_from(edges)

    def strongly_connected_components(self) -> tuple[tuple[str, ...], ...]:
        return tuple(
            sorted(
                tuple(sorted(group))
                for group in nx.strongly_connected_components(self._graph)
            )
        )

    def bounded_witness(
        self, members: tuple[str, ...], *, start: str
    ) -> tuple[tuple[str, str], ...]:
        # A subgraph view shares the prepared graph's storage and adjacency order.
        return tuple(nx.find_cycle(self._graph.subgraph(members), source=start))


class NetworkXGraphAlgorithms(GraphAlgorithms):
    def prepare(
        self, nodes: tuple[str, ...], edges: tuple[tuple[str, str], ...]
    ) -> GraphHandle:
        return NetworkXGraphHandle(nodes, edges)
