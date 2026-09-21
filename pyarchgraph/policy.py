"""Explicit selection of evidence for structural architecture checks."""

from dataclasses import dataclass

from pyarchgraph.model import DependencyEdge, ImportFact, ImportScope, ResolutionKind


GRAPH_POLICY_VERSION = "structural-v1"
TEST_EXCLUDES = ("tests", "test_*.py", "*_test.py")
DEFINITE_KINDS = frozenset({ResolutionKind.EXACT_MODULE, ResolutionKind.EXACT_BASE})


@dataclass(frozen=True, slots=True)
class GraphPolicy:
    """Structural defaults include typing and local imports, exclude test code.

    Excluding local or typing evidence is a choice of graph, not proof that
    initialization succeeds. Dynamic literal targets remain uncertain.
    """

    include_type_only: bool = True
    include_local: bool = True
    include_tests: bool = False


def filter_dependencies(
    dependencies: tuple[DependencyEdge, ...],
    facts: tuple[ImportFact, ...],
    policy: GraphPolicy,
    *,
    definite_only: bool = False,
) -> tuple[DependencyEdge, ...]:
    by_id = {fact.id: fact for fact in facts}
    selected = []
    for edge in dependencies:
        evidence = tuple(
            item
            for item in edge.evidence
            if (not definite_only or item.resolution_kind in DEFINITE_KINDS)
            and (policy.include_type_only or not by_id[item.fact_id].type_only)
            and (
                policy.include_local or by_id[item.fact_id].scope is ImportScope.MODULE
            )
        )
        if evidence:
            selected.append(DependencyEdge(edge.source, edge.target, evidence))
    return tuple(selected)
