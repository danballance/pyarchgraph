"""Package projections preserve source identities, filtering and provenance."""

from dataclasses import FrozenInstanceError, replace

import pytest

from pyarchgraph.domain.graph import AnalysisSnapshot
from pyarchgraph.domain.models import (
    ImportContext,
    ImportFact,
    ImportSyntax,
    ResolutionKind,
    SourceModule,
    UnresolvedReason,
)
from pyarchgraph.domain.resolution import (
    ArchitectureDependencyPolicy,
    StaticImportResolver,
)
from pyarchgraph.domain.strategies import (
    EvidenceInterpreter,
    ModuleBodyView,
    NonTypingView,
    PackageView,
    StructuralView,
)
from pyarchgraph.domain.validation import GraphValidator, SnapshotNormalizer


def _source(name, *, package=False, binding_status="bound"):
    path = name.replace(".", "/") + ("/__init__.py" if package else ".py")
    return SourceModule(
        id=f"source:{path}",
        path=path,
        is_package=package,
        parent_package=name.rpartition(".")[0] or None,
        import_name=name,
        binding_status=binding_status,
    )


def _fact(source, requested, *, key="fact", child=None, context=ImportContext()):
    return ImportFact(
        id=key,
        source=source.id,
        path=source.path,
        line=2,
        column=0,
        end_line=2,
        end_column=20,
        alias_index=0,
        syntax=ImportSyntax.IMPORT_FROM if child else ImportSyntax.IMPORT,
        source_segment=(
            f"from {requested} import {child}" if child else f"import {requested}"
        ),
        base_module=requested,
        imported_name=child,
        as_name=None,
        bound_name=child or requested.partition(".")[0],
        relative_level=0,
        context=context,
    )


def _snapshot(sources, facts=(), *, namespaces=()):
    result = StaticImportResolver().resolve(facts, sources, namespaces)
    return AnalysisSnapshot(
        sources=sources,
        facts=facts,
        dependencies=ArchitectureDependencyPolicy().select(result.dependencies),
        external_imports=result.external_imports,
        unresolved_imports=result.unresolved_imports,
        resolved_dependencies=result.dependencies,
    )


def test_immediate_packages_own_initializers_and_namespace_modules():
    sources = (
        _source("app", package=True),
        _source("app.main"),
        _source("app.nested", package=True),
        _source("app.nested.worker"),
        _source("plugins.readers.csv"),
    )
    graph = PackageView(StructuralView()).transform(_snapshot(sources))
    assert {node.id: node.members for node in graph.nodes} == {
        "package:app": (sources[0].id, sources[1].id),
        "package:app.nested": (sources[2].id, sources[3].id),
        "package:plugins.readers": (sources[4].id,),
    }
    assert [node.label for node in graph.nodes] == [
        "app",
        "app.nested",
        "plugins.readers",
    ]


def test_standalone_and_unbound_sources_keep_distinct_source_nodes():
    sources = (
        _source("main"),
        _source("app.path", binding_status="path_only"),
        _source("app.shadowed", binding_status="shadowed"),
        _source("app.ambiguous", binding_status="ambiguous"),
        _source("app.bad-name"),
        _source("app.is"),
        replace(_source("app.unknown"), import_name=None),
    )
    graph = PackageView(StructuralView()).transform(_snapshot(sources))
    assert {node.id: node.members for node in graph.nodes} == {
        source.id: (source.id,) for source in sources
    }


@pytest.mark.parametrize("reversed_order", [False, True])
def test_opaque_singleton_ids_cannot_silently_merge_with_package_nodes(reversed_order):
    standalone = replace(_source("main"), id="package:pkg")
    member = _source("pkg.leaf")
    sources = (member, standalone) if reversed_order else (standalone, member)
    with pytest.raises(ValueError, match="conflicts with a singleton source ID"):
        PackageView(StructuralView()).transform(_snapshot(sources))


def test_opaque_singleton_ids_with_package_prefix_remain_valid_without_a_collision():
    standalone = replace(_source("main"), id="package:unrelated")
    member = _source("pkg.leaf")
    graph = PackageView(StructuralView()).transform(_snapshot((standalone, member)))
    assert {node.id: node.members for node in graph.nodes} == {
        "package:unrelated": (standalone.id,),
        "package:pkg": (member.id,),
    }


@pytest.mark.parametrize("depth", [1, 2, 3, 8])
def test_depth_caps_package_name_without_consuming_standalone_modules(depth):
    sources = (
        _source("app", package=True),
        _source("app.services.deep.worker"),
        _source("standalone"),
    )
    graph = PackageView(StructuralView(), depth).transform(_snapshot(sources))
    expected = ".".join(("app", "services", "deep")[:depth])
    by_source = {source: node.id for node in graph.nodes for source in node.members}
    assert by_source == {
        sources[0].id: "package:app",
        sources[1].id: f"package:{expected}",
        sources[2].id: sources[2].id,
    }


@pytest.mark.parametrize("depth", [0, -1, True, False, 1.0, "2"])
def test_depth_requires_a_positive_integer(depth):
    with pytest.raises(ValueError, match="positive integer"):
        PackageView(StructuralView(), depth)


def test_package_view_configuration_is_immutable():
    view = PackageView(StructuralView())
    with pytest.raises(FrozenInstanceError):
        view.max_depth = 2


def test_projection_merges_external_pairs_and_discards_internal_dependencies():
    left = _source("left.a")
    sibling = _source("left.b")
    right = _source("right.target")
    facts = (
        _fact(left, "right.target", key="cross-a"),
        _fact(sibling, "right.target", key="cross-b"),
        _fact(left, "left.b", key="internal"),
        _fact(left, "left.a", key="self"),
    )
    snapshot = _snapshot((left, sibling, right), facts)
    graph = PackageView(StructuralView()).transform(snapshot)
    (edge,) = graph.dependencies
    assert (edge.source, edge.target) == ("package:left", "package:right")
    assert {item.fact_id for item in edge.evidence} == {"cross-a", "cross-b"}
    assert graph.retained_fact_ids == tuple(sorted(fact.id for fact in facts))
    assert GraphValidator().prepare(snapshot).normalize(graph) == graph
    reordered = replace(
        snapshot,
        sources=tuple(reversed(snapshot.sources)),
        facts=tuple(reversed(snapshot.facts)),
        dependencies=tuple(reversed(snapshot.dependencies)),
    )
    assert PackageView(StructuralView()).transform(reordered) == graph


@pytest.mark.parametrize(
    ("source_view", "retained"),
    [
        (StructuralView(), {"normal", "typing", "function"}),
        (NonTypingView(), {"normal", "function"}),
        (ModuleBodyView(), {"normal"}),
    ],
)
def test_source_filters_apply_to_selected_and_restored_evidence(source_view, retained):
    left = _source("left.caller")
    package = _source("right", package=True)
    child = _source("right.worker")
    facts = tuple(
        _fact(left, "right", key=key, child="worker", context=context)
        for key, context in (
            ("normal", ImportContext()),
            ("typing", ImportContext(typing_only=True)),
            ("function", ImportContext(scope="function", in_function=True)),
        )
    )
    graph = PackageView(source_view).transform(_snapshot((left, package, child), facts))
    assert set(graph.retained_fact_ids) == retained
    (edge,) = graph.dependencies
    assert {(item.fact_id, item.resolution_kind) for item in edge.evidence} == {
        (key, kind)
        for key in retained
        for kind in (ResolutionKind.EXACT_BASE, ResolutionKind.PROBABLE_SUBMODULE)
    }


def test_raw_evidence_strengthens_the_same_package_pair_and_preserves_provenance():
    left = _source("left.caller")
    package = _source("right", package=True)
    child = _source("right.worker")
    fact = _fact(left, "right", child="worker")
    snapshot = _snapshot((left, package, child), (fact,))
    module_graph = StructuralView().transform(snapshot)
    assert [item.resolution_kind for item in module_graph.dependencies[0].evidence] == [
        ResolutionKind.PROBABLE_SUBMODULE
    ]
    graph = PackageView(StructuralView()).transform(snapshot)
    assert GraphValidator().prepare(snapshot).normalize(graph) == graph
    (edge,) = graph.dependencies
    assert {(item.target, item.resolution_kind) for item in edge.evidence} == {
        (package.id, ResolutionKind.EXACT_BASE),
        (child.id, ResolutionKind.PROBABLE_SUBMODULE),
    }
    assert StructuralView().transform(snapshot) == module_graph
    reported = EvidenceInterpreter().dependency(edge, {fact.id: fact})
    assert (reported.source, reported.target) == ("package:left", "package:right")
    assert {item.target for item in reported.evidence} == {package.id, child.id}
    assert all(
        item.source == left.id and item.fact_id == fact.id for item in reported.evidence
    )
    assert all(
        item.path == left.path and item.line == 2 and item.column == 1
        for item in reported.evidence
    )
    definite = EvidenceInterpreter().dependency(
        edge, {fact.id: fact}, definite_only=True
    )
    assert [item.target for item in definite.evidence] == [package.id]


def test_different_parent_package_is_restored_only_after_depth_rollup():
    left = _source("left.caller")
    parent = _source("right", package=True)
    child = _source("right.nested", package=True)
    snapshot = _snapshot((left, parent, child), (_fact(left, "right", child="nested"),))
    (edge,) = PackageView(StructuralView()).transform(snapshot).dependencies
    assert edge.target == "package:right.nested"
    assert [item.resolution_kind for item in edge.evidence] == [
        ResolutionKind.PROBABLE_SUBMODULE
    ]
    (rolled,) = PackageView(StructuralView(), 1).transform(snapshot).dependencies
    assert rolled.target == "package:right"
    assert {item.resolution_kind for item in rolled.evidence} == {
        ResolutionKind.EXACT_BASE,
        ResolutionKind.PROBABLE_SUBMODULE,
    }


@pytest.mark.parametrize("raw", [None, ()])
def test_legacy_or_empty_raw_evidence_does_not_invent_certainty(raw):
    left = _source("left.caller")
    package = _source("right", package=True)
    child = _source("right.worker")
    snapshot = replace(
        _snapshot((left, package, child), (_fact(left, "right", child="worker"),)),
        resolved_dependencies=raw,
    )
    (edge,) = PackageView(StructuralView()).transform(snapshot).dependencies
    assert [item.resolution_kind for item in edge.evidence] == [
        ResolutionKind.PROBABLE_SUBMODULE
    ]
    assert SnapshotNormalizer().normalize(snapshot).resolved_dependencies is raw


def test_restoring_raw_support_does_not_allow_fabricated_evidence():
    left = _source("left.caller")
    package = _source("right", package=True)
    child = _source("right.worker")
    snapshot = _snapshot(
        (left, package, child), (_fact(left, "right", child="worker"),)
    )
    graph = PackageView(StructuralView()).transform(snapshot)
    (edge,) = graph.dependencies
    forged = replace(edge.evidence[0], resolution_kind=ResolutionKind.EXACT_MODULE)
    graph = replace(graph, dependencies=(replace(edge, evidence=(forged,)),))
    with pytest.raises(ValueError, match="not supported"):
        GraphValidator().prepare(snapshot).normalize(graph)


def test_snapshot_normalization_keeps_and_orders_full_resolution_evidence():
    left = _source("left.caller")
    package = _source("right", package=True)
    child = _source("right.worker")
    snapshot = _snapshot(
        (left, package, child), (_fact(left, "right", child="worker"),)
    )
    raw = tuple(
        replace(edge, evidence=edge.evidence + edge.evidence)
        for edge in reversed(snapshot.resolved_dependencies)
    )
    normalized = SnapshotNormalizer().normalize(
        replace(snapshot, resolved_dependencies=raw)
    )
    assert normalized.resolved_dependencies == snapshot.resolved_dependencies
    assert normalized.dependencies == snapshot.dependencies
    assert SnapshotNormalizer().normalize(normalized) is normalized


def test_namespace_base_imports_remain_unmodelled_instead_of_inventing_source_edges():
    left = _source("left.caller")
    child = _source("namespace.readers.worker")
    fact = _fact(left, "namespace.readers")
    snapshot = _snapshot(
        (left, child), (fact,), namespaces=("namespace", "namespace.readers")
    )
    graph = PackageView(StructuralView()).transform(snapshot)
    assert graph.dependencies == ()
    assert [item.reason for item in snapshot.unresolved_imports] == [
        UnresolvedReason.NAMESPACE_BASE_UNMODELLED
    ]
    assert {node.id for node in graph.nodes} == {
        "package:left",
        "package:namespace.readers",
    }


def test_internal_exact_and_probable_evidence_cannot_reintroduce_self_edges():
    package = _source("right", package=True)
    caller = _source("right.caller")
    child = _source("right.worker")
    snapshot = _snapshot(
        (package, caller, child), (_fact(caller, "right", child="worker"),)
    )
    assert PackageView(StructuralView()).transform(snapshot).dependencies == ()
