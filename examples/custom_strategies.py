"""External strategies: package projection and advisory group-size findings.

Run from the repository root: python -m examples.custom_strategies src
These classes only consume the public extension contract; no engine or JSON
renderer changes are needed. A projected cycle need not be a module cycle.
"""

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from pyarchgraph import (
    AnalysisSnapshot,
    ApplicationFactory,
    CheckContext,
    CheckRegistration,
    CheckResult,
    RuleFinding,
    Severity,
    ViewEdge,
    ViewEvidence,
    ViewGraph,
    ViewNode,
    ViewRegistration,
)


class PackageGroupingView:
    """Group by top-level import name, dropping internal group dependencies."""

    def transform(self, snapshot: AnalysisSnapshot) -> ViewGraph:
        groups: dict[str, list[str]] = defaultdict(list)
        owner = {}
        for source in snapshot.sources:
            label = (
                source.import_name.partition(".")[0]
                if source.import_name
                else Path(source.path).parts[0]
            )
            node_id = "package:" + label
            groups[node_id].append(source.id)
            owner[source.id] = node_id
        support: dict[tuple[str, str], list[ViewEvidence]] = defaultdict(list)
        for edge in snapshot.dependencies:
            pair = owner[edge.source], owner[edge.target]
            if pair[0] == pair[1]:
                continue
            support[pair].extend(
                ViewEvidence(
                    edge.source, edge.target, item.fact_id, item.resolution_kind
                )
                for item in edge.evidence
            )
        return ViewGraph(
            nodes=tuple(
                ViewNode(
                    node_id, node_id.removeprefix("package:"), tuple(sorted(members))
                )
                for node_id, members in sorted(groups.items())
            ),
            dependencies=tuple(
                ViewEdge(source, target, tuple(evidence))
                for (source, target), evidence in sorted(support.items())
            ),
            retained_fact_ids=tuple(fact.id for fact in snapshot.facts),
        )


@dataclass(frozen=True)
class GroupSizeCheck:
    maximum_sources: int = 20

    def evaluate(self, context: CheckContext) -> tuple[CheckResult, ...]:
        return tuple(
            CheckResult(
                severity=Severity.WARNING,
                finding=RuleFinding(
                    code="large_source_group",
                    message=f"{node.label} contains {len(node.members)} source modules.",
                    node_ids=(node.id,),
                    source_ids=node.members,
                ),
            )
            for node in context.graph.nodes
            if len(node.members) > self.maximum_sources
        )


class ExampleApplication:
    def create_factory(self) -> ApplicationFactory:
        return ApplicationFactory(
            views=ApplicationFactory.default_views()
            + (ViewRegistration("packages", PackageGroupingView()),),
            checks=ApplicationFactory.default_checks()
            + (CheckRegistration("group-size", GroupSizeCheck(), ("packages",)),),
            check_selection={
                "packages": ("cycles", "unresolved-imports", "group-size"),
                "non-typing": (),
            },
        )

    def run(self) -> int:
        return self.create_factory().create_cli().run()


if __name__ == "__main__":
    raise SystemExit(ExampleApplication().run())
