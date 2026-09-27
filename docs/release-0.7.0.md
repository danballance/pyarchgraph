# 0.7.0 release candidate

This coordinated runtime change introduces a ports and adapters architecture,
pluggable graph views and checks, Python API 0.7, and JSON schema 0.7.
The verified pre-change baseline was 428 passing tests and 40 successful corpus
runs. Publication requires migrating Checksmith's strict schema 0.6 adapter in
its own repository first.

## Python migration

Construct the incoming analysis port through composition:

```python
from pathlib import Path
from pyarchgraph import AnalysisOptions, ApplicationFactory, JsonReportRenderer

analyzer = ApplicationFactory().create_analyzer()
report = analyzer.analyse(
    (Path("src"),),
    options=AnalysisOptions(gate="structural"),
    base_dir=Path("/workspace/project"),
)
output = JsonReportRenderer().render(report)
```

The free functions `analyse` and `render_json` and the old flat runtime modules
are removed without wrappers. Import collaborators from their defining modules
under `domain`, `application`, or `adapters`. Core services use injected protocols;
only `composition.py` chooses concrete adapters. Services can be reused because
per-analysis mutable state belongs to private sessions.

Implementations explicitly inherit their protocols. `ImportResolver` is defined
in `pyarchgraph.domain.resolution`; import it there rather than from
`pyarchgraph.application.ports`.

Read views by exact registered IDs: `report.views["non-typing"]`. The default
IDs remain `structural`, `non-typing`, and `module-body`; underscores are not
aliases. Select a custom registered ID through `AnalysisOptions(gate=...)`.
Explicit `base_dir` controls relative input resolution and report paths. With no
base directory, the working directory is captured once for the operation.

## Report migration

Each view includes `nodes`, `enabled_check_ids`, `dependency_count`,
`cyclic_dependency_count`, `cyclic_node_count`, and `findings`.
`cyclic_node_count` replaces `cyclic_source_count` because a projected node can
represent several original source modules. Each finding is an envelope:

```json
{
  "check_id": "cycles",
  "severity": "error",
  "finding": {"kind": "cycle", "certainty": "definite"}
}
```

The example above abbreviates the cycle payload. Built-in cycle and unresolved
payloads retain their existing fields. Unresolved findings also identify their
projected `node`; locations carry original `source`, `target`, and `fact_id`
provenance. Rule extensions emit the tagged `rule` payload with code, message,
node/source references, and evidence. Serialization needs no extension dispatch.

Incomplete coverage has precedence and returns exit 2. Otherwise only selected
view error findings return exit 1. Warning and information findings return exit
0. Invalid input, configuration, or extension output returns stderr and exit 2
without a report. Expected source read and parse failures still return partial
reports. Summary reports retain bounded witnesses; full component edges remain
opt-in through `details="component-edges"`.

## Extension registration

View and check objects implement independent protocols. Built-ins and supplied
examples explicitly inherit them; external implementations can still use
structural typing without inheritance. Returned graphs and findings use the
public frozen dataclasses, with immutable tuple collections. Dataclass subclasses
with extra fields are rejected
to keep the schema and validation contract fixed. Register strategy objects
through `ApplicationFactory` or an immutable
`StrategyRegistry`. Defaults preserve all three existing views and checks
`cycles` and `unresolved-imports`. A check registration can restrict applicable
view IDs. Per-view `check_selection` chooses an exact check set, including empty,
for standard or custom views. Duplicate/reserved IDs, unknown references, invalid
applicability, and unknown gates are rejected before discovery.

A view receives immutable original sources, facts, dependencies, and resolution
records. Node memberships must be nonempty, disjoint sets of original source
IDs. Every retained edge must be supported by original endpoints, fact IDs, and
resolution kinds. Explicit self-loops are honored. Aggregation may create cycles
of projected nodes even where individual modules are acyclic; the package
example intentionally discards edges internal to a group.

Checks receive retained facts and resolution records, original sources, the
validated view, and cycle analysis. Topology metrics are calculated regardless
of which checks are enabled. Coverage validation cannot be removed. Extension
exceptions and invalid outputs raise chained `ExtensionError` identifying the
extension and its view.

See [the external example](../examples/custom_strategies.py). Historical research
artifacts retain their original observations and hashes. The delivery fixture and
reference collector now declare their protocols explicitly; the collector also
imports the current domain models directly. The fixture's current hash and added
dependency are recorded separately from the historical baseline. Active tests,
evaluator, benchmarks, and replay instrumentation target 0.7.

[Verification record](verification-0.7.md) contains the supported Python matrix,
corpus, packaging, replay, and paired benchmark results and commands.
