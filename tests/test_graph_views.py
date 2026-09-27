"""Views select evidence before resolving graph certainty and components."""

from dataclasses import replace

import pytest

from pyarchgraph.adapters.networkx_graph import NetworkXGraphAlgorithms
from pyarchgraph.application.strategies import (
    CheckRegistration,
    StrategyEngine,
    StrategyRegistry,
    ViewRegistration,
)
from pyarchgraph.domain.graph import AnalysisSnapshot
from pyarchgraph.domain.model import (
    DependencyEdge,
    DependencyEvidence,
    ImportContext,
    ImportFact,
    ImportSyntax,
    ResolutionKind,
    SourceModule,
    UnresolvedImport,
    UnresolvedReason,
)
from pyarchgraph.domain.strategies import (
    CycleCheck,
    ModuleBodyView,
    NonTypingView,
    StructuralView,
    UnresolvedImportCheck,
)


def evaluate_views(dependencies, facts, unresolved, *, details="summary"):
    sources = tuple(
        SourceModule(source, f"{source}.py", False, source)
        for source in sorted(
            {fact.source for fact in facts}
            | {source for edge in dependencies for source in (edge.source, edge.target)}
        )
    )
    registry = StrategyRegistry(
        (
            ViewRegistration("structural", StructuralView()),
            ViewRegistration("non-typing", NonTypingView()),
            ViewRegistration("module-body", ModuleBodyView()),
        ),
        (
            CheckRegistration("cycles", CycleCheck()),
            CheckRegistration("unresolved-imports", UnresolvedImportCheck()),
        ),
    )
    snapshot = AnalysisSnapshot(
        sources, facts, dependencies, unresolved_imports=unresolved
    )
    return StrategyEngine(registry, NetworkXGraphAlgorithms()).evaluate(
        snapshot, details=details
    )


def fact(source, target, *, line=1, context=ImportContext(), alias=0):
    return ImportFact(
        id=f"{source}-{target}-{line}-{alias}",
        source=source,
        path=f"{source}.py",
        line=line,
        column=0,
        end_line=line,
        end_column=10,
        alias_index=alias,
        syntax=ImportSyntax.IMPORT,
        source_segment=f"import {target}",
        base_module=target,
        imported_name=None,
        as_name=None,
        bound_name=target,
        relative_level=0,
        context=context,
    )


def edge(source, target, *facts, kind=ResolutionKind.EXACT_MODULE):
    return DependencyEdge(
        source, target, tuple(DependencyEvidence(item.id, kind) for item in facts)
    )


def test_views_filter_sites_and_keep_mixed_edge_support():
    typing = fact("a", "b", line=1, context=ImportContext(typing_only=True))
    local = fact(
        "a", "b", line=2, context=ImportContext(scope="function", in_function=True)
    )
    module = fact("a", "b", line=3)
    reverse = fact("b", "a")
    facts = (typing, local, module, reverse)
    views = evaluate_views(
        (edge("a", "b", *facts[:3]), edge("b", "a", reverse)), facts, ()
    )
    for view, lines in (
        (views["structural"], [1, 2, 3]),
        (views["non-typing"], [2, 3]),
        (views["module-body"], [3]),
    ):
        assert view.dependency_count == view.cyclic_dependency_count == 2
        assert view.cyclic_node_count == 2
        forward = next(
            item for item in view.findings[0].finding.witness if item.source == "a"
        )
        assert [item.line for item in forward.evidence] == lines


def test_excluding_exact_support_downgrades_to_possible():
    typing = fact("a", "b", context=ImportContext(typing_only=True))
    probable = fact("a", "b", line=2)
    reverse = fact("b", "a")
    forward = DependencyEdge(
        "a",
        "b",
        (
            DependencyEvidence(typing.id, ResolutionKind.EXACT_BASE),
            DependencyEvidence(probable.id, ResolutionKind.PROBABLE_SUBMODULE),
        ),
    )
    views = evaluate_views(
        (forward, edge("b", "a", reverse)), (typing, probable, reverse), ()
    )
    assert views["structural"].findings[0].finding.certainty == "definite"
    assert views["non-typing"].findings[0].finding.certainty == "possible"
    assert views["non-typing"].findings[0].finding.definite_members == ()


def test_local_import_fix_changes_module_body_view_only():
    forward, reverse = fact("a", "b"), fact("b", "a")
    dependencies = (edge("a", "b", forward), edge("b", "a", reverse))
    before = evaluate_views(dependencies, (forward, reverse), ())
    deferred = replace(
        forward, context=ImportContext(scope="function", in_function=True)
    )
    after = evaluate_views(dependencies, (deferred, reverse), ())
    assert (
        before["structural"].dependency_count
        == after["structural"].dependency_count
        == 2
    )
    assert (
        before["non-typing"].cyclic_node_count
        == after["non-typing"].cyclic_node_count
        == 2
    )
    assert before["module-body"].cyclic_node_count == 2
    assert after["module-body"].dependency_count == 1
    assert after["module-body"].findings == ()


def test_typing_self_loop_is_absent_from_narrower_views():
    item = fact("a", "a", context=ImportContext(typing_only=True))
    views = evaluate_views((edge("a", "a", item),), (item,), ())
    assert (
        views["structural"].cyclic_node_count
        == views["structural"].cyclic_dependency_count
        == 1
    )
    assert (
        views["non-typing"].dependency_count
        == views["module-body"].dependency_count
        == 0
    )


def test_unresolved_findings_follow_import_context():
    typing = fact("a", "missing", context=ImportContext(typing_only=True))
    local = fact("a", "missing", line=2, context=ImportContext(in_function=True))
    unresolved = UnresolvedImport(
        "a", "missing", UnresolvedReason.MISSING_INTERNAL_TARGET, (typing.id, local.id)
    )
    views = evaluate_views((), (typing, local), (unresolved,))
    assert len(views["structural"].findings[0].finding.evidence) == 2
    assert [item.line for item in views["non-typing"].findings[0].finding.evidence] == [
        2
    ]
    assert views["module-body"].findings == ()


def test_public_evidence_deduplicates_aliases_but_not_locations_or_kinds():
    first = fact("a", "b")
    duplicate = fact("a", "b", alias=1)
    another = fact("a", "b", line=2)
    reverse = fact("b", "a")
    forward = DependencyEdge(
        "a",
        "b",
        (
            DependencyEvidence(first.id, ResolutionKind.EXACT_BASE),
            DependencyEvidence(duplicate.id, ResolutionKind.EXACT_BASE),
            DependencyEvidence(first.id, ResolutionKind.EXACT_MODULE),
            DependencyEvidence(another.id, ResolutionKind.EXACT_BASE),
        ),
    )
    unresolved = UnresolvedImport(
        "a", "b", UnresolvedReason.RELATIVE_ESCAPE, (first.id, duplicate.id, another.id)
    )
    result = evaluate_views(
        (forward, edge("b", "a", reverse)),
        (first, duplicate, another, reverse),
        (unresolved,),
    )
    result = tuple(item.finding for item in result["structural"].findings)
    displayed = next(item for item in result[0].witness if item.source == "a")
    assert [(item.line, item.resolution_kind) for item in displayed.evidence] == [
        (1, ResolutionKind.EXACT_BASE),
        (1, ResolutionKind.EXACT_MODULE),
        (2, ResolutionKind.EXACT_BASE),
    ]
    assert [item.line for item in result[1].evidence] == [1, 2]


def test_component_details_include_nonwitness_edges_and_summary_counts():
    pairs = (("a", "b"), ("b", "a"), ("b", "c"), ("c", "b"), ("d", "e"))
    facts = tuple(
        fact(source, target, line=index + 1)
        for index, (source, target) in enumerate(pairs)
    )
    edges = tuple(
        edge(source, target, item) for (source, target), item in zip(pairs, facts)
    )
    summary = evaluate_views(edges, facts, ())
    detail = evaluate_views(edges, facts, (), details="component-edges")
    view = detail["structural"]
    assert view.dependency_count == 5
    assert view.cyclic_node_count == 3
    assert view.cyclic_dependency_count == 4
    cycle = view.findings[0].finding
    assert cycle.dependency_count == 4
    assert len(cycle.witness) == 2
    assert [(item.source, item.target) for item in cycle.dependencies] == list(
        pairs[:4]
    )
    assert summary["structural"].findings[0].finding.dependencies is None
    assert (
        evaluate_views(
            tuple(reversed(edges)),
            tuple(reversed(facts)),
            (),
            details="component-edges",
        )
        == detail
    )


def test_filter_can_split_component_and_increase_findings():
    pairs = (("a", "b"), ("b", "a"), ("b", "c"), ("c", "d"), ("d", "c"), ("d", "a"))
    facts = tuple(
        fact(
            source,
            target,
            context=ImportContext(
                typing_only=(source, target) in {("b", "c"), ("d", "a")}
            ),
        )
        for source, target in pairs
    )
    edges = tuple(
        edge(source, target, item) for (source, target), item in zip(pairs, facts)
    )
    views = evaluate_views(edges, facts, ())
    assert len(views["structural"].findings) == 1
    assert len(views["non-typing"].findings) == 2
    assert (
        views["structural"].cyclic_node_count
        == views["non-typing"].cyclic_node_count
        == 4
    )
    assert views["non-typing"].cyclic_dependency_count == 4


def test_unknown_detail_setting_fails_explicitly():
    with pytest.raises(ValueError, match="details"):
        evaluate_views((), (), (), details="all-cycles")
