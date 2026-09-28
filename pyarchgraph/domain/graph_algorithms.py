"""The domain's graph-algorithm port, with no graph-library types."""

from typing import Protocol


class GraphHandle(Protocol):
    """Provide reusable component and witness queries for one analysis's graph."""

    def strongly_connected_components(self) -> tuple[tuple[str, ...], ...]: ...

    def bounded_witness(
        self, members: tuple[str, ...], *, start: str
    ) -> tuple[tuple[str, str], ...]:
        """Return the first DFS cycle from start, respecting prepared edge order.

        Restrict traversal to members and retain successor insertion order from
        prepare(). Return only the closed cycle's ordered edges, with no repeated
        source and no acyclic prefix. The domain supplies sorted edges and chooses
        start, so replacing the graph library preserves witness-selection policy.
        Never enumerate all simple cycles.
        """
        ...


class GraphAlgorithms(Protocol):
    """Define how cycle analysis prepares a graph without choosing a graph library."""

    def prepare(
        self, nodes: tuple[str, ...], edges: tuple[tuple[str, str], ...]
    ) -> GraphHandle:
        """Prepare once, preserving the domain-provided node and edge order."""
        ...
