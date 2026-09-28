"""Extensions compose through contracts and preserve graph provenance."""

from dataclasses import FrozenInstanceError, replace
from types import MappingProxyType

import pytest

from pyarchgraph.adapters.driven.networkx_graph import NetworkXGraphAlgorithms
from pyarchgraph.application.exceptions import AnalysisError, ExtensionError
from pyarchgraph.application.strategies import (
    CheckRegistration,
    StrategyEngine,
    StrategyRegistry,
    ViewRegistration,
)
from pyarchgraph.domain.graph import (
    AnalysisSnapshot,
    ViewEdge,
    ViewEvidence,
    ViewGraph,
    ViewNode,
)
from pyarchgraph.domain.graph_algorithms import GraphAlgorithms, GraphHandle
from pyarchgraph.domain.models import (
    CheckResult,
    DependencyEdge,
    DependencyEvidence,
    ExternalClassification,
    ExternalImport,
    ImportContext,
    ImportFact,
    ImportSyntax,
    ResolutionKind,
    RuleFinding,
    Severity,
    SourceModule,
    UnresolvedImport,
    UnresolvedReason,
)
from pyarchgraph.domain.strategies import (
    CheckStrategy,
    CycleCheck,
    GraphViewStrategy,
    NonTypingView,
    StructuralView,
    UnresolvedImportCheck,
)


def _fact(source, target, *, index=1, typing_only=False):
    return ImportFact(
        id=f"{source}:{index}",
        source=source,
        path=f"{source}.py",
        line=index,
        column=0,
        end_line=index,
        end_column=8,
        alias_index=0,
        syntax=ImportSyntax.IMPORT,
        source_segment=f"import {target}",
        base_module=target,
        imported_name=None,
        as_name=None,
        bound_name=target,
        relative_level=0,
        context=ImportContext(typing_only=typing_only),
    )


def _snapshot(pairs=(("a", "b"), ("b", "a"))):
    facts = tuple(
        _fact(source, target, index=index + 1)
        for index, (source, target) in enumerate(pairs)
    )
    sources = tuple(
        SourceModule(name, f"{name}.py", False, None, import_name=name)
        for name in sorted({name for pair in pairs for name in pair})
    )
    return AnalysisSnapshot(
        sources,
        facts,
        tuple(
            DependencyEdge(
                source,
                target,
                (DependencyEvidence(fact.id, ResolutionKind.EXACT_MODULE),),
            )
            for (source, target), fact in zip(pairs, facts)
        ),
    )


def _registry(*, views=None, checks=None, selection=None):
    return StrategyRegistry(
        views
        if views is not None
        else (ViewRegistration("structural", StructuralView()),),
        checks
        if checks is not None
        else (
            CheckRegistration("cycles", CycleCheck()),
            CheckRegistration("unresolved-imports", UnresolvedImportCheck()),
        ),
        selection or {},
    )


def _engine(registry=None):
    return StrategyEngine(registry or _registry(), NetworkXGraphAlgorithms())


class AdvisoryCheck(CheckStrategy):
    def evaluate(self, context):
        return (
            CheckResult(
                Severity.WARNING,
                RuleFinding(
                    "advice",
                    "Consider a smaller dependency surface.",
                    node_ids=(context.graph.nodes[0].id,),
                    source_ids=(context.sources[0].id,),
                ),
            ),
        )


class GroupedView(GraphViewStrategy):
    """An external view can project sources without changing the engine."""

    def __init__(self, groups, *, self_loops=False):
        self.groups = groups
        self.self_loops = self_loops

    def transform(self, snapshot):
        owner = {
            member: node for node, members in self.groups.items() for member in members
        }
        evidence = {}
        for edge in snapshot.dependencies:
            pair = owner[edge.source], owner[edge.target]
            if pair[0] != pair[1] or self.self_loops:
                evidence.setdefault(pair, []).extend(
                    ViewEvidence(
                        edge.source, edge.target, item.fact_id, item.resolution_kind
                    )
                    for item in edge.evidence
                )
        return ViewGraph(
            tuple(
                ViewNode(node, f"Group {node}", tuple(members))
                for node, members in self.groups.items()
            ),
            tuple(ViewEdge(*pair, tuple(items)) for pair, items in evidence.items()),
            tuple(fact.id for fact in snapshot.facts),
        )


def test_removing_all_checks_keeps_topology_metrics():
    result = _engine(_registry(selection={"structural": ()})).evaluate(_snapshot())[
        "structural"
    ]
    assert result.enabled_check_ids == result.findings == ()
    assert (
        result.dependency_count
        == result.cyclic_dependency_count
        == result.cyclic_node_count
        == 2
    )


def test_check_selection_on_standard_and_custom_views_and_applicability():
    registry = _registry(
        views=(
            ViewRegistration("structural", StructuralView()),
            ViewRegistration("custom", StructuralView()),
        ),
        checks=(CheckRegistration("advisory", AdvisoryCheck(), ("custom",)),),
        selection={"structural": (), "custom": ("advisory",)},
    )
    result = _engine(registry).evaluate(_snapshot())
    assert result["structural"].findings == ()
    (finding,) = result["custom"].findings
    assert finding.check_id == "advisory"
    assert finding.severity is Severity.WARNING
    assert finding.finding.code == "advice"
    with pytest.raises(AnalysisError, match="not applicable"):
        _registry(
            views=registry.views,
            checks=registry.checks,
            selection={"structural": ("advisory",)},
        )


def test_registries_and_report_view_mappings_are_immutable():
    selection = {"structural": []}
    registry = _registry(selection=selection)
    selection["structural"].append("cycles")
    assert registry.check_selection["structural"] == ()
    with pytest.raises(TypeError):
        registry.check_selection["structural"] = ("cycles",)
    with pytest.raises(FrozenInstanceError):
        registry.checks = ()
    views = _engine(registry).evaluate(_snapshot())
    assert isinstance(views, MappingProxyType)
    with pytest.raises(TypeError):
        views["structural"] = views["structural"]


def test_aggregate_cycle_is_a_cycle_of_nodes_with_original_evidence():
    # Original graph is acyclic. Aggregating a and c induces X -> Y -> X.
    snapshot = _snapshot((("a", "b"), ("b", "c")))
    grouped = GroupedView({"X": ("a", "c"), "Y": ("b",)})
    result = _engine(_registry(views=(ViewRegistration("groups", grouped),))).evaluate(
        snapshot
    )["groups"]
    (finding,) = result.findings
    cycle = finding.finding
    assert cycle.members == ("X", "Y")
    assert result.cyclic_node_count == 2
    assert {
        (item.source, item.target) for edge in cycle.witness for item in edge.evidence
    } == {("a", "b"), ("b", "c")}
    assert all(item.fact_id for edge in cycle.witness for item in edge.evidence)


def test_grouping_can_drop_internal_edges_or_explicitly_retain_self_loops():
    snapshot = _snapshot()
    dropped = GroupedView({"group": ("a", "b")})
    retained = GroupedView({"group": ("a", "b")}, self_loops=True)
    results = _engine(
        _registry(
            views=(
                ViewRegistration("drop", dropped),
                ViewRegistration("keep", retained),
            )
        )
    ).evaluate(snapshot)
    assert results["drop"].dependency_count == 0
    assert results["keep"].dependency_count == results["keep"].cyclic_node_count == 1
    assert results["keep"].findings[0].finding.members == ("group",)


class AlteredView(GraphViewStrategy):
    def __init__(self, alter):
        self.alter = alter

    def transform(self, snapshot):
        return self.alter(StructuralView().transform(snapshot))


@pytest.mark.parametrize(
    "alter",
    [
        lambda graph: replace(
            graph,
            nodes=(replace(graph.nodes[0], members=("unknown",)),) + graph.nodes[1:],
        ),
        lambda graph: replace(
            graph,
            nodes=(
                graph.nodes[0],
                replace(graph.nodes[1], members=graph.nodes[0].members),
            ),
        ),
        lambda graph: replace(
            graph, nodes=(replace(graph.nodes[0], members=()),) + graph.nodes[1:]
        ),
        lambda graph: replace(graph, retained_fact_ids=()),
        lambda graph: replace(
            graph,
            dependencies=(
                replace(graph.dependencies[0], target=graph.dependencies[0].source),
            ),
        ),
        lambda graph: replace(
            graph,
            dependencies=(
                replace(
                    graph.dependencies[0],
                    evidence=(
                        replace(graph.dependencies[0].evidence[0], fact_id="invented"),
                    ),
                ),
            ),
        ),
        lambda graph: replace(
            graph,
            dependencies=(
                replace(
                    graph.dependencies[0],
                    evidence=(
                        replace(
                            graph.dependencies[0].evidence[0],
                            resolution_kind=ResolutionKind.PROBABLE_SUBMODULE,
                        ),
                    ),
                ),
            ),
        ),
        lambda graph: replace(
            graph, dependencies=(replace(graph.dependencies[0], evidence=()),)
        ),
        lambda graph: replace(graph, nodes=list(graph.nodes)),
        lambda graph: None,
    ],
)
def test_invalid_graph_and_fabricated_provenance_raise_chained_extension_errors(alter):
    engine = _engine(
        _registry(views=(ViewRegistration("invalid", AlteredView(alter)),))
    )
    with pytest.raises(
        ExtensionError, match="view extension 'invalid'.*view 'invalid'"
    ) as caught:
        engine.evaluate(_snapshot())
    assert caught.value.__cause__ is not None


class ObservingCheck(CheckStrategy):
    def __init__(self):
        self.contexts = []

    def evaluate(self, context):
        self.contexts.append(context)
        return ()


def test_retained_facts_filter_resolution_records_before_checks():
    snapshot = _snapshot((("a", "b"),))
    ordinary = _fact("a", "missing", index=10)
    guarded = _fact("a", "missing", index=11, typing_only=True)
    snapshot = replace(
        snapshot,
        facts=snapshot.facts + (ordinary, guarded),
        unresolved_imports=(
            UnresolvedImport(
                "a",
                "missing",
                UnresolvedReason.MISSING_INTERNAL_TARGET,
                (ordinary.id, guarded.id),
            ),
        ),
        external_imports=(
            ExternalImport(
                "a", "external", ExternalClassification.EXTERNAL_UNKNOWN, (guarded.id,)
            ),
        ),
    )
    check = ObservingCheck()
    registry = _registry(
        views=(ViewRegistration("non-typing", NonTypingView()),),
        checks=(CheckRegistration("observe", check),),
    )
    _engine(registry).evaluate(snapshot)
    (context,) = check.contexts
    assert context.unresolved_imports[0].fact_ids == (ordinary.id,)
    assert context.external_imports == ()
    assert guarded.id not in {fact.id for fact in context.facts}


def test_aggregate_unresolved_findings_retain_source_and_view_node():
    snapshot = _snapshot()
    fact = _fact("a", "missing", index=10)
    snapshot = replace(
        snapshot,
        facts=snapshot.facts + (fact,),
        unresolved_imports=(
            UnresolvedImport(
                "a", "missing", UnresolvedReason.MISSING_INTERNAL_TARGET, (fact.id,)
            ),
        ),
    )
    result = _engine(
        _registry(views=(ViewRegistration("groups", GroupedView({"X": ("a", "b")})),))
    ).evaluate(snapshot)["groups"]
    (finding,) = result.findings
    assert finding.finding.source == "a"
    assert finding.finding.node == "X"
    assert finding.finding.evidence[0].source == "a"


class FailingCheck(CheckStrategy):
    def __init__(self):
        self.fail = True

    def evaluate(self, context):
        if self.fail:
            raise RuntimeError("extension failure")
        return ()


def test_engine_reuse_after_success_and_failure_does_not_retain_results():
    check = FailingCheck()
    engine = _engine(_registry(checks=(CheckRegistration("custom", check),)))
    with pytest.raises(
        ExtensionError, match="check extension 'custom'.*view 'structural'"
    ) as caught:
        engine.evaluate(_snapshot())
    assert isinstance(caught.value.__cause__, RuntimeError)
    check.fail = False
    assert engine.evaluate(_snapshot())["structural"].cyclic_node_count == 2
    assert (
        engine.evaluate(_snapshot((("a", "b"),)))["structural"].cyclic_node_count == 0
    )


@pytest.mark.parametrize(
    "output",
    [
        [],
        (object(),),
        (CheckResult("warning", RuleFinding("x", "x")),),
        (CheckResult(Severity.INFO, RuleFinding("x", "x", node_ids=("unknown",))),),
    ],
)
def test_invalid_check_outputs_raise_contextual_extension_errors(output):
    class InvalidCheck(CheckStrategy):
        def evaluate(self, context):
            return output

    engine = _engine(_registry(checks=(CheckRegistration("invalid", InvalidCheck()),)))
    with pytest.raises(
        ExtensionError, match="check extension 'invalid'.*view 'structural'"
    ):
        engine.evaluate(_snapshot())


def test_views_receive_the_same_independent_normalized_snapshot():
    seen = []

    class Observer(GraphViewStrategy):
        def transform(self, snapshot):
            seen.append(snapshot)
            return StructuralView().transform(snapshot)

    original = _snapshot()
    original = replace(
        original,
        sources=tuple(reversed(original.sources)),
        facts=tuple(reversed(original.facts)),
    )
    engine = _engine(
        _registry(
            views=(
                ViewRegistration("one", Observer()),
                ViewRegistration("two", Observer()),
            )
        )
    )
    first = engine.evaluate(original)
    second = engine.evaluate(_snapshot())
    assert seen[0] is seen[1]
    assert seen[0].sources == tuple(
        sorted(original.sources, key=lambda source: source.id)
    )
    assert first == second


def test_core_engine_uses_injected_graph_algorithm_port():
    class Handle(GraphHandle):
        def strongly_connected_components(self):
            return (("a",), ("b",))

        def bounded_witness(self, members, *, start):
            raise AssertionError("acyclic graph should not query a witness")

    class Algorithms(GraphAlgorithms):
        def __init__(self):
            self.calls = []

        def prepare(self, nodes, edges):
            self.calls.append((nodes, edges))
            return Handle()

    algorithms = Algorithms()
    result = StrategyEngine(_registry(), algorithms).evaluate(_snapshot((("a", "b"),)))[
        "structural"
    ]
    assert result.cyclic_node_count == 0
    assert len(algorithms.calls) == 2
    assert algorithms.calls[0] == (("a", "b"), (("a", "b"),))


class OutputCheck(CheckStrategy):
    def __init__(self, output):
        self.output = output

    def evaluate(self, context):
        return self.output(context)


def _evaluate_output(output, *, snapshot=None):
    registry = _registry(checks=(CheckRegistration("custom", OutputCheck(output)),))
    return _engine(registry).evaluate(snapshot or _snapshot())


def _rule_evidence(context):
    from pyarchgraph.domain.strategies import EvidenceInterpreter

    fact = context.facts[0]
    return EvidenceInterpreter.location(fact)


@pytest.mark.parametrize(
    "changes",
    [
        {"line": True},
        {"line": 1.0},
        {"column": True},
        {"column": 1.0},
        {"source_segment": ["import b"]},
        {"resolution_kind": "exact_module"},
        {"context": ImportContext(typing_only=0)},
        {"context": ImportContext(conditional=0)},
        {"context": ImportContext(in_function=0)},
        {"context": ImportContext(exception_handler=0)},
        {"context": ImportContext(package_initializer=0)},
        {"context": ImportContext(scope="invalid")},
        {"source": 1},
        {"target": []},
        {"fact_id": []},
    ],
)
def test_malformed_evidence_scalars_and_contexts_are_rejected(changes):
    def output(context):
        evidence = replace(_rule_evidence(context), **changes)
        return (
            CheckResult(
                Severity.INFO, RuleFinding("custom", "Message", evidence=(evidence,))
            ),
        )

    with pytest.raises(ExtensionError, match="check extension 'custom'"):
        _evaluate_output(output)


@pytest.mark.parametrize(
    "target", ["result", "rule", "evidence", "context", "cycle", "dependency"]
)
def test_public_output_subclasses_cannot_leak_mutable_fields(target):
    from dataclasses import field, fields, make_dataclass

    from pyarchgraph.domain.models import (
        CycleFinding,
        EvidenceLocation,
        FindingDependency,
    )

    def extended(value):
        subclass = make_dataclass(
            "ExtendedValue",
            [
                (
                    "mutable_extra",
                    list,
                    field(default_factory=list, hash=False, compare=False),
                )
            ],
            bases=(type(value),),
            frozen=True,
            slots=True,
        )
        return subclass(
            **{
                item.name: getattr(value, item.name)
                for item in fields(value)
                if item.init
            }
        )

    def output(context):
        if target == "cycle":
            assert type(context.cycle_analysis[0]) is CycleFinding
            finding = extended(context.cycle_analysis[0])
        elif target == "dependency":
            cycle = context.cycle_analysis[0]
            assert type(cycle.witness[0]) is FindingDependency
            finding = replace(
                cycle, witness=(extended(cycle.witness[0]),) + cycle.witness[1:]
            )
        else:
            evidence = _rule_evidence(context)
            if target == "evidence":
                assert type(evidence) is EvidenceLocation
                evidence = extended(evidence)
            elif target == "context":
                evidence = replace(evidence, context=extended(evidence.context))
            finding = RuleFinding("custom", "Message", evidence=(evidence,))
            if target == "rule":
                finding = extended(finding)
        result = CheckResult(Severity.INFO, finding)
        return (extended(result) if target == "result" else result,)

    with pytest.raises(ExtensionError, match="check extension 'custom'"):
        _evaluate_output(output)


@pytest.mark.parametrize("field_name", ["nodes", "dependencies", "evidence"])
def test_graph_output_subclasses_cannot_leak_mutable_fields(field_name):
    from dataclasses import field, fields, make_dataclass

    def alter(graph):
        value = {
            "nodes": graph.nodes[0],
            "dependencies": graph.dependencies[0],
            "evidence": graph.dependencies[0].evidence[0],
        }[field_name]
        subtype = make_dataclass(
            "ExtendedGraphValue",
            [
                (
                    "mutable_extra",
                    list,
                    field(default_factory=list, hash=False, compare=False),
                )
            ],
            bases=(type(value),),
            frozen=True,
            slots=True,
        )
        extended = subtype(
            **{
                item.name: getattr(value, item.name)
                for item in fields(value)
                if item.init
            }
        )
        if field_name == "nodes":
            return replace(graph, nodes=(extended,) + graph.nodes[1:])
        if field_name == "dependencies":
            return replace(graph, dependencies=(extended,) + graph.dependencies[1:])
        edge = replace(graph.dependencies[0], evidence=(extended,))
        return replace(graph, dependencies=(edge,) + graph.dependencies[1:])

    with pytest.raises(ExtensionError, match="view extension 'custom'"):
        _engine(
            _registry(views=(ViewRegistration("custom", AlteredView(alter)),))
        ).evaluate(_snapshot())


def test_copied_cycle_rejects_bool_count_equal_to_integer_count():
    def output(context):
        return (
            CheckResult(
                Severity.INFO, replace(context.cycle_analysis[0], dependency_count=True)
            ),
        )

    with pytest.raises(ExtensionError, match="nonnegative integers"):
        _evaluate_output(output, snapshot=_snapshot((("a", "a"),)))


def test_canonical_cycle_copy_is_supported():
    def output(context):
        return (CheckResult(Severity.WARNING, replace(context.cycle_analysis[0])),)

    result = _evaluate_output(output)["structural"]
    assert result.findings[0].severity is Severity.WARNING


@pytest.mark.parametrize(
    "changes",
    [
        {"path": "invented.py", "fact_id": None},
        {"target": "invented"},
        {"resolution_kind": ResolutionKind.PROBABLE_SUBMODULE},
    ],
)
def test_custom_rule_cannot_fabricate_evidence(changes):
    def output(context):
        return (
            CheckResult(
                Severity.INFO,
                RuleFinding(
                    "custom",
                    "Message",
                    evidence=(replace(_rule_evidence(context), **changes),),
                ),
            ),
        )

    with pytest.raises(ExtensionError, match="evidence"):
        _evaluate_output(output)


def test_supported_evidence_without_fact_id_remains_valid():
    def output(context):
        return (
            CheckResult(
                Severity.INFO,
                RuleFinding(
                    "custom",
                    "Message",
                    evidence=(replace(_rule_evidence(context), fact_id=None),),
                ),
            ),
        )

    assert _evaluate_output(output)["structural"].findings[0].finding.evidence


def test_graph_adapter_preserves_domain_provided_witness_traversal_order():
    algorithms = NetworkXGraphAlgorithms()
    nodes = ("a", "b", "c")
    edges = (("a", "c"), ("c", "a"), ("a", "b"), ("b", "a"))
    handle = algorithms.prepare(nodes, edges)
    assert handle.bounded_witness(nodes, start="a") == (("a", "c"), ("c", "a"))


def test_domain_supplies_sorted_edges_and_witness_start_to_graph_port():
    class RecordingHandle(GraphHandle):
        def __init__(self, handle, calls):
            self.handle = handle
            self.calls = calls

        def strongly_connected_components(self):
            return self.handle.strongly_connected_components()

        def bounded_witness(self, members, *, start):
            self.calls.append((members, start))
            return self.handle.bounded_witness(members, start=start)

    class RecordingAlgorithms(GraphAlgorithms):
        def __init__(self):
            self.prepared = []
            self.witnesses = []

        def prepare(self, nodes, edges):
            self.prepared.append((nodes, edges))
            return RecordingHandle(
                NetworkXGraphAlgorithms().prepare(nodes, edges), self.witnesses
            )

    algorithms = RecordingAlgorithms()
    snapshot = _snapshot((("c", "a"), ("a", "c"), ("b", "a"), ("a", "b")))
    result = StrategyEngine(_registry(), algorithms).evaluate(snapshot)["structural"]
    assert algorithms.prepared[0] == (
        ("a", "b", "c"),
        (("a", "b"), ("a", "c"), ("b", "a"), ("c", "a")),
    )
    assert algorithms.witnesses == [(("a", "b", "c"), "a")]
    assert tuple(
        (edge.source, edge.target) for edge in result.findings[0].finding.witness
    ) == (("a", "b"), ("b", "a"))


def test_reused_engine_rebuilds_provenance_after_success_and_view_failure():
    class RememberingView(GraphViewStrategy):
        def __init__(self):
            self.previous = None

        def transform(self, snapshot):
            if self.previous is None:
                self.previous = StructuralView().transform(snapshot)
            return self.previous

    view = RememberingView()
    engine = _engine(_registry(views=(ViewRegistration("custom", view),)))
    first = _snapshot((("a", "b"),))
    second = _snapshot((("a", "c"),))
    second = replace(second, sources=second.sources + (first.sources[1],))
    assert engine.evaluate(first)["custom"].dependency_count == 1
    # The same fact ID now resolves elsewhere. The old supported edge must fail.
    with pytest.raises(ExtensionError, match="not supported"):
        engine.evaluate(second)
    view.previous = None
    assert engine.evaluate(second)["custom"].dependency_count == 1
    view.previous = None
    assert engine.evaluate(first)["custom"].dependency_count == 1


def test_snapshot_normalization_remains_immutable_and_differentially_equivalent():
    from pyarchgraph.domain.validation import SnapshotNormalizer

    canonical = _snapshot()
    first_fact = canonical.facts[0]
    canonical = replace(
        canonical,
        external_imports=(
            ExternalImport(
                first_fact.source,
                "external",
                ExternalClassification.EXTERNAL_UNKNOWN,
                (first_fact.id,),
            ),
        ),
        unresolved_imports=(
            UnresolvedImport(
                first_fact.source,
                "missing",
                UnresolvedReason.MISSING_INTERNAL_TARGET,
                (first_fact.id,),
            ),
        ),
    )
    changed = replace(
        canonical,
        sources=list(reversed(canonical.sources)),
        facts=list(reversed(canonical.facts)),
        dependencies=tuple(
            replace(edge, evidence=list(edge.evidence) * 2)
            for edge in reversed(canonical.dependencies)
        ),
        external_imports=tuple(
            replace(item, fact_ids=list(item.fact_ids))
            for item in canonical.external_imports
        ),
        unresolved_imports=tuple(
            replace(item, fact_ids=list(item.fact_ids) * 2)
            for item in canonical.unresolved_imports
        ),
    )
    normalized = SnapshotNormalizer().normalize(changed)
    assert normalized == canonical
    assert type(normalized.sources) is type(normalized.facts) is tuple
    assert all(type(edge.evidence) is tuple for edge in normalized.dependencies)
    assert all(
        type(item.fact_ids) is tuple
        for item in normalized.external_imports + normalized.unresolved_imports
    )
    assert _engine().evaluate(changed) == _engine().evaluate(canonical)
    changed.dependencies[0].evidence.clear()
    changed.external_imports[0].fact_ids.clear()
    assert normalized == canonical
