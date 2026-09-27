"""Views select evidence before resolving graph certainty and components."""

from dataclasses import replace

import pytest

from pyarchgraph.findings import build_findings, build_views
from pyarchgraph.model import (
    DependencyEdge,
    DependencyEvidence,
    ImportContext,
    ImportFact,
    ImportSyntax,
    ResolutionKind,
    UnresolvedImport,
    UnresolvedReason,
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
    views = build_views(
        (edge("a", "b", *facts[:3]), edge("b", "a", reverse)), facts, ()
    )
    for view, lines in (
        (views.structural, [1, 2, 3]),
        (views.non_typing, [2, 3]),
        (views.module_body, [3]),
    ):
        assert view.dependency_count == view.cyclic_dependency_count == 2
        assert view.cyclic_source_count == 2
        forward = next(item for item in view.findings[0].witness if item.source == "a")
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
    views = build_views(
        (forward, edge("b", "a", reverse)), (typing, probable, reverse), ()
    )
    assert views.structural.findings[0].certainty == "definite"
    assert views.non_typing.findings[0].certainty == "possible"
    assert views.non_typing.findings[0].definite_members == ()


def test_local_import_fix_changes_module_body_view_only():
    forward, reverse = fact("a", "b"), fact("b", "a")
    dependencies = (edge("a", "b", forward), edge("b", "a", reverse))
    before = build_views(dependencies, (forward, reverse), ())
    deferred = replace(
        forward, context=ImportContext(scope="function", in_function=True)
    )
    after = build_views(dependencies, (deferred, reverse), ())
    assert before.structural.dependency_count == after.structural.dependency_count == 2
    assert (
        before.non_typing.cyclic_source_count
        == after.non_typing.cyclic_source_count
        == 2
    )
    assert before.module_body.cyclic_source_count == 2
    assert after.module_body.dependency_count == 1
    assert after.module_body.findings == ()


def test_typing_self_loop_is_absent_from_narrower_views():
    item = fact("a", "a", context=ImportContext(typing_only=True))
    views = build_views((edge("a", "a", item),), (item,), ())
    assert (
        views.structural.cyclic_source_count
        == views.structural.cyclic_dependency_count
        == 1
    )
    assert views.non_typing.dependency_count == views.module_body.dependency_count == 0


def test_unresolved_findings_follow_import_context():
    typing = fact("a", "missing", context=ImportContext(typing_only=True))
    local = fact("a", "missing", line=2, context=ImportContext(in_function=True))
    unresolved = UnresolvedImport(
        "a", "missing", UnresolvedReason.MISSING_INTERNAL_TARGET, (typing.id, local.id)
    )
    views = build_views((), (typing, local), (unresolved,))
    assert len(views.structural.findings[0].evidence) == 2
    assert [item.line for item in views.non_typing.findings[0].evidence] == [2]
    assert views.module_body.findings == ()


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
    result = build_findings(
        (forward, edge("b", "a", reverse)),
        (first, duplicate, another, reverse),
        (unresolved,),
    )
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
    summary = build_views(edges, facts, ())
    detail = build_views(edges, facts, (), details="component-edges")
    view = detail.structural
    assert view.dependency_count == 5
    assert view.cyclic_source_count == 3
    assert view.cyclic_dependency_count == 4
    cycle = view.findings[0]
    assert cycle.dependency_count == 4
    assert len(cycle.witness) == 2
    assert [(item.source, item.target) for item in cycle.dependencies] == list(
        pairs[:4]
    )
    assert summary.structural.findings[0].dependencies is None
    assert (
        build_views(
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
    views = build_views(edges, facts, ())
    assert len(views.structural.findings) == 1
    assert len(views.non_typing.findings) == 2
    assert (
        views.structural.cyclic_source_count
        == views.non_typing.cyclic_source_count
        == 4
    )
    assert views.non_typing.cyclic_dependency_count == 4


def test_unknown_detail_setting_fails_explicitly():
    with pytest.raises(ValueError, match="details"):
        build_views((), (), (), details="all-cycles")
