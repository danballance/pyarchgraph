"""Normalize immutable analysis values and validate contextual provenance."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType

from pyarchgraph.domain.graph import (
    AnalysisSnapshot,
    CheckContext,
    ViewEdge,
    ViewEvidence,
    ViewGraph,
    ViewNode,
)
from pyarchgraph.domain.models import (
    CheckResult,
    CycleFinding,
    EvidenceLocation,
    FindingDependency,
    ImportContext,
    ImportFact,
    ImportFinding,
    ResolutionKind,
    RuleFinding,
    Severity,
)


class GraphValidator:
    """Prepare original source evidence for validating views before checks run."""

    def prepare(self, snapshot: AnalysisSnapshot) -> _GraphValidationSession:
        """Build immutable provenance indexes once for this analysis only."""
        return _GraphValidationSession(
            source_ids=frozenset(source.id for source in snapshot.sources),
            facts=MappingProxyType({fact.id: fact for fact in snapshot.facts}),
            support=frozenset(
                (edge.source, edge.target, item.fact_id, item.resolution_kind)
                for edge in (
                    *snapshot.dependencies,
                    *(snapshot.resolved_dependencies or ()),
                )
                for item in edge.evidence
            ),
        )


@dataclass(frozen=True, slots=True)
class _GraphValidationSession:
    """Validate and order a view using one snapshot's source membership and evidence."""

    source_ids: frozenset[str]
    facts: Mapping[str, ImportFact]
    support: frozenset[tuple[str, str, str, ResolutionKind]]

    def normalize(self, graph: ViewGraph) -> ViewGraph:
        if type(graph) is not ViewGraph:
            raise ValueError("a graph strategy must return ViewGraph")
        if not all(
            type(value) is tuple
            for value in (graph.nodes, graph.dependencies, graph.retained_fact_ids)
        ):
            raise ValueError("graph collections must be immutable tuples")
        source_ids = self.source_ids
        facts = self.facts
        support = self.support
        owner: dict[str, str] = {}
        nodes: dict[str, ViewNode] = {}
        for node in graph.nodes:
            if type(node) is not ViewNode:
                raise ValueError("graph nodes must be ViewNode values")
            if type(node.id) is not str or not node.id or node.id in nodes:
                raise ValueError("view node IDs must be nonempty and unique")
            if type(node.label) is not str or not node.label:
                raise ValueError("view node labels must be nonempty strings")
            if type(node.members) is not tuple or not node.members:
                raise ValueError("view nodes require nonempty immutable memberships")
            for member in node.members:
                if (
                    type(member) is not str
                    or member not in source_ids
                    or member in owner
                ):
                    raise ValueError(
                        "view memberships must be disjoint original sources"
                    )
                owner[member] = node.id
            members = (
                node.members if len(node.members) == 1 else tuple(sorted(node.members))
            )
            nodes[node.id] = (
                node
                if members == node.members
                else ViewNode(node.id, node.label, members)
            )
        if not all(type(fact_id) is str for fact_id in graph.retained_fact_ids):
            raise ValueError("retained fact IDs must be strings")
        retained = set(graph.retained_fact_ids)
        if (
            len(retained) != len(graph.retained_fact_ids)
            or not retained <= facts.keys()
        ):
            raise ValueError("retained fact IDs must be unique original facts")
        if any(facts[fact_id].source not in owner for fact_id in retained):
            raise ValueError("retained facts must belong to a projected source")
        edges: dict[tuple[str, str], ViewEdge] = {}
        for edge in graph.dependencies:
            if type(edge) is not ViewEdge:
                raise ValueError("graph dependencies must be ViewEdge values")
            if type(edge.source) is not str or type(edge.target) is not str:
                raise ValueError("view edge endpoints must be string node IDs")
            pair = (edge.source, edge.target)
            if edge.source not in nodes or edge.target not in nodes or pair in edges:
                raise ValueError("view edges require unique pairs of existing nodes")
            if type(edge.evidence) is not tuple or not edge.evidence:
                raise ValueError("view edges require nonempty immutable evidence")
            for item in edge.evidence:
                if (
                    type(item) is not ViewEvidence
                    or type(item.resolution_kind) is not ResolutionKind
                ):
                    raise ValueError("edge evidence must be ViewEvidence values")
                if (
                    type(item.source) is not str
                    or type(item.target) is not str
                    or type(item.fact_id) is not str
                ):
                    raise ValueError("view evidence references must be string IDs")
                original = (
                    item.source,
                    item.target,
                    item.fact_id,
                    item.resolution_kind,
                )
                if original not in support or item.fact_id not in retained:
                    raise ValueError(
                        "view evidence is not supported by retained dependencies"
                    )
                if (owner.get(item.source), owner.get(item.target)) != pair:
                    raise ValueError(
                        "view evidence endpoints disagree with node memberships"
                    )
                if facts[item.fact_id].source != item.source:
                    raise ValueError("view evidence fact does not belong to its source")
            evidence = (
                edge.evidence
                if len(edge.evidence) == 1
                else tuple(sorted(set(edge.evidence), key=self._evidence_key))
            )
            edges[pair] = (
                edge
                if evidence == edge.evidence
                else ViewEdge(edge.source, edge.target, evidence)
            )
        normalized_nodes = tuple(nodes[key] for key in sorted(nodes))
        normalized_edges = tuple(edges[key] for key in sorted(edges))
        normalized_retained = tuple(sorted(retained))
        if (
            normalized_nodes == graph.nodes
            and normalized_edges == graph.dependencies
            and normalized_retained == graph.retained_fact_ids
        ):
            return graph
        return ViewGraph(normalized_nodes, normalized_edges, normalized_retained)

    @staticmethod
    def _evidence_key(item: ViewEvidence) -> tuple[str, str, str, str]:
        return item.source, item.target, item.fact_id, item.resolution_kind.value


class SnapshotNormalizer:
    """Order and deduplicate snapshot values before they are shared with views."""

    def normalize(self, snapshot: AnalysisSnapshot) -> AnalysisSnapshot:
        sources = tuple(sorted(snapshot.sources, key=self._id))
        facts = tuple(sorted(snapshot.facts, key=self._id))
        dependencies = tuple(
            sorted(
                (self._dependency(item) for item in snapshot.dependencies),
                key=self._edge,
            )
        )
        resolved = (
            None
            if snapshot.resolved_dependencies is None
            else tuple(
                sorted(
                    (self._dependency(item) for item in snapshot.resolved_dependencies),
                    key=self._edge,
                )
            )
        )
        external = tuple(
            sorted(
                (self._record(item) for item in snapshot.external_imports),
                key=self._external,
            )
        )
        unresolved = tuple(
            sorted(
                (self._record(item) for item in snapshot.unresolved_imports),
                key=self._unresolved,
            )
        )
        if (
            type(snapshot) is AnalysisSnapshot
            and all(
                type(value) is tuple
                for value in (
                    snapshot.sources,
                    snapshot.facts,
                    snapshot.dependencies,
                    snapshot.external_imports,
                    snapshot.unresolved_imports,
                )
            )
            and sources == snapshot.sources
            and facts == snapshot.facts
            and dependencies == snapshot.dependencies
            and (
                snapshot.resolved_dependencies is None
                or type(snapshot.resolved_dependencies) is tuple
            )
            and resolved == snapshot.resolved_dependencies
            and external == snapshot.external_imports
            and unresolved == snapshot.unresolved_imports
        ):
            return snapshot
        return AnalysisSnapshot(
            sources, facts, dependencies, external, unresolved, resolved
        )

    def _dependency(self, edge):
        evidence = (
            tuple(edge.evidence)
            if len(edge.evidence) < 2
            else tuple(sorted(set(edge.evidence), key=self._evidence))
        )
        return (
            edge
            if type(edge.evidence) is tuple and evidence == edge.evidence
            else replace(edge, evidence=evidence)
        )

    @staticmethod
    def _record(item):
        ids = (
            tuple(item.fact_ids)
            if len(item.fact_ids) < 2
            else tuple(sorted(set(item.fact_ids)))
        )
        return (
            item
            if type(item.fact_ids) is tuple and ids == item.fact_ids
            else replace(item, fact_ids=ids)
        )

    @staticmethod
    def _id(item):
        return item.id

    @staticmethod
    def _edge(item):
        return item.source, item.target

    @staticmethod
    def _evidence(item):
        return item.fact_id, item.resolution_kind.value

    @staticmethod
    def _external(item):
        return item.source, item.requested, item.classification.value

    @staticmethod
    def _unresolved(item):
        return item.source, item.requested, item.reason.value


class CheckResultValidator:
    """Validate immutable check results and their evidence against the analysed view."""

    def validate(self, results: tuple[CheckResult, ...], context: CheckContext) -> None:
        if type(results) is not tuple:
            raise ValueError(
                "a check must return an immutable tuple of CheckResult values"
            )
        if not results:
            return
        for result in results:
            if type(result) is not CheckResult or type(result.severity) is not Severity:
                raise ValueError(
                    "a check must return CheckResult values with a Severity"
                )
        # The common cycle check returns domain-created objects. Their shapes
        # and evidence are already known, so avoid rebuilding provenance indexes.
        cycle_ids = {id(cycle) for cycle in context.cycle_analysis}
        if all(
            type(result.finding) is CycleFinding and id(result.finding) in cycle_ids
            for result in results
        ):
            return
        nodes = {node.id for node in context.graph.nodes}
        sources = {source.id for source in context.sources}
        owner = {
            member: node.id for node in context.graph.nodes for member in node.members
        }
        facts = {fact.id: fact for fact in context.facts}
        support = {}
        for edge in context.graph.dependencies:
            for item in edge.evidence:
                support.setdefault(item.fact_id, []).append(item)
        unresolved = {}
        for item in context.unresolved_imports:
            unresolved.setdefault(
                (item.source, item.requested, item.reason.value), set()
            ).update(item.fact_ids)
        for result in results:
            finding = result.finding
            if type(finding) is RuleFinding:
                if type(finding.kind) is not str or finding.kind != "rule":
                    raise ValueError("rule findings require the 'rule' kind")
                self._validate_message(finding.code, finding.message)
                self._validate_ids(finding.node_ids, "node")
                self._validate_ids(finding.source_ids, "source")
                if not set(finding.node_ids) <= nodes:
                    raise ValueError(
                        "rule finding node references must be existing node IDs"
                    )
                if not set(finding.source_ids) <= sources:
                    raise ValueError(
                        "rule finding source references must be original source IDs"
                    )
                self._validate_evidence(finding.evidence, facts, support)
            elif type(finding) is CycleFinding:
                if id(finding) not in cycle_ids:
                    self._validate_cycle_shape(finding)
                    if finding not in context.cycle_analysis:
                        raise ValueError(
                            "cycle findings must be supported by the view's cycle analysis"
                        )
            elif type(finding) is ImportFinding:
                if type(finding.kind) is not str or finding.kind != "unresolved_import":
                    raise ValueError(
                        "unresolved findings require the 'unresolved_import' kind"
                    )
                self._validate_message(finding.code, finding.message)
                if type(finding.source) is not str or type(finding.node) is not str:
                    raise ValueError(
                        "unresolved finding source and node IDs must be strings"
                    )
                if finding.requested is not None and type(finding.requested) is not str:
                    raise ValueError(
                        "unresolved finding requested names must be strings"
                    )
                if finding.source not in owner or finding.node != owner[finding.source]:
                    raise ValueError(
                        "unresolved findings require their original source and view node"
                    )
                allowed_fact_ids = unresolved.get(
                    (finding.source, finding.requested, finding.code)
                )
                if allowed_fact_ids is None:
                    raise ValueError(
                        "unresolved findings must reference retained resolution records"
                    )
                self._validate_evidence(
                    finding.evidence, facts, support, allowed_fact_ids=allowed_fact_ids
                )
            else:
                raise ValueError(
                    "unsupported finding payload; use the public RuleFinding type for custom rules"
                )

    @staticmethod
    def _validate_message(code: str, message: str) -> None:
        if type(code) is not str or not code or type(message) is not str or not message:
            raise ValueError("findings require a nonempty string code and message")

    @staticmethod
    def _validate_ids(ids: tuple[str, ...], description: str) -> None:
        if type(ids) is not tuple or not all(
            type(item) is str and item for item in ids
        ):
            raise ValueError(
                f"finding {description} references must be immutable string IDs"
            )

    def _validate_cycle_shape(self, finding: CycleFinding) -> None:
        if type(finding.kind) is not str or finding.kind != "cycle":
            raise ValueError("cycle findings require the 'cycle' kind")
        if type(finding.certainty) is not str or finding.certainty not in (
            "definite",
            "possible",
        ):
            raise ValueError("cycle certainty must be 'definite' or 'possible'")
        if type(finding.dependency_count) is not int or finding.dependency_count < 0:
            raise ValueError("cycle dependency counts must be nonnegative integers")
        self._validate_ids(finding.members, "member")
        self._validate_ids(finding.definite_members, "definite member")
        dependency_groups = (finding.witness,)
        if finding.dependencies is not None:
            dependency_groups += (finding.dependencies,)
        for dependencies in dependency_groups:
            if type(dependencies) is not tuple:
                raise ValueError("cycle dependencies must be immutable tuples")
            for edge in dependencies:
                if (
                    type(edge) is not FindingDependency
                    or type(edge.source) is not str
                    or type(edge.target) is not str
                ):
                    raise ValueError(
                        "cycle dependencies must be public FindingDependency values with string endpoints"
                    )
                self._validate_evidence_shape(edge.evidence)

    @staticmethod
    def _validate_evidence_shape(evidence: tuple[EvidenceLocation, ...]) -> None:
        if type(evidence) is not tuple or not all(
            type(item) is EvidenceLocation for item in evidence
        ):
            raise ValueError(
                "finding evidence must be an immutable tuple of public EvidenceLocation values"
            )
        for item in evidence:
            if type(item.path) is not str or not item.path:
                raise ValueError("evidence paths must be nonempty strings")
            if (
                type(item.line) is not int
                or item.line < 1
                or type(item.column) is not int
                or item.column < 1
            ):
                raise ValueError("evidence positions must be positive integers")
            if item.source_segment is not None and type(item.source_segment) is not str:
                raise ValueError("evidence source segments must be strings")
            if (
                item.resolution_kind is not None
                and type(item.resolution_kind) is not ResolutionKind
            ):
                raise ValueError(
                    "evidence resolution kinds must be ResolutionKind values"
                )
            for reference in (item.source, item.target, item.fact_id):
                if reference is not None and (
                    type(reference) is not str or not reference
                ):
                    raise ValueError("evidence references must be nonempty string IDs")
            context = item.context
            if type(context) is not ImportContext:
                raise ValueError(
                    "evidence contexts must be public ImportContext values"
                )
            if type(context.scope) is not str or context.scope not in (
                "module",
                "class",
                "function",
            ):
                raise ValueError("evidence context scope is invalid")
            if not all(
                type(value) is bool
                for value in (
                    context.in_function,
                    context.typing_only,
                    context.conditional,
                    context.exception_handler,
                    context.package_initializer,
                )
            ):
                raise ValueError("evidence context flags must be booleans")

    def _validate_evidence(
        self,
        evidence: tuple[EvidenceLocation, ...],
        facts: dict[str, ImportFact],
        support: dict,
        *,
        allowed_fact_ids: set[str] | None = None,
    ) -> None:
        self._validate_evidence_shape(evidence)
        for item in evidence:
            candidates = (facts[item.fact_id],) if item.fact_id in facts else ()
            if item.fact_id is None:
                candidates = tuple(facts.values())
            matched = tuple(
                fact
                for fact in candidates
                if (
                    item.path,
                    item.line,
                    item.column,
                    item.source_segment,
                    item.context,
                )
                == (
                    fact.path,
                    fact.line,
                    fact.column + 1,
                    fact.source_segment,
                    fact.context,
                )
                and (item.source is None or item.source == fact.source)
                and (allowed_fact_ids is None or fact.id in allowed_fact_ids)
            )
            if not matched:
                raise ValueError(
                    "finding evidence must match a retained original import fact"
                )
            if item.target is not None or item.resolution_kind is not None:
                if not any(
                    edge.fact_id == fact.id
                    and edge.source == fact.source
                    and (item.target is None or item.target == edge.target)
                    and (
                        item.resolution_kind is None
                        or item.resolution_kind is edge.resolution_kind
                    )
                    for fact in matched
                    for edge in support.get(fact.id, ())
                ):
                    raise ValueError(
                        "finding evidence endpoints and resolution must match retained graph support"
                    )
