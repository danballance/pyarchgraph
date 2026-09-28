# Package views and JSON schema 0.8

Package analysis reuses the existing source collection, parsing, resolution,
graph algorithms and checks. It adds package projection and complete dependency
reporting. This document describes the schema migration; publishing a package
release and migrating consumers in other repositories are separate work.

## CLI and Python options

The default composition now contains six views. `structural`, `non-typing` and
`module-body` retain their source-level behaviour. Their package equivalents are
`package-structural`, `package-non-typing` and `package-module-body`. The default
gate is still `structural`; package views remain informational unless selected.
Incomplete coverage still takes precedence over findings from any selected gate.

```console
uv run pyarchgraph src --gate package-structural
uv run pyarchgraph src --gate package-module-body --package-max-depth 2
```

The new `AnalysisOptions.package_max_depth: int | None = None` matches the CLI
option. It accepts positive integers, validated before source access. Omission
keeps each module's immediate containing package. Depth 1 groups by the first
component of the dotted package name; depth 2 keeps up to two components.
Package initializers belong to their own package. Standalone modules and sources
without reliable import bindings remain individual source nodes. TOML continues
to describe ownership and target boundaries, rather than view options.

Package nodes have stable `package:<dotted-name>` IDs and disjoint source
memberships. Filtering precedes grouping. Projection merges evidence between
shared endpoints, removes edges inside a resulting node and retains isolated
nodes. Parent package nodes do not also own child-package sources. Package cycles
can exist without module cycles because different members can establish each
direction of a package relationship.

Namespace packages work without `__init__.py`. Imports of source modules within
them contribute dependencies. Imports targeting only namespace containers have
no source target and do not create graph edges. Coverage limitations state this
restriction; no initializer files need to be added.

## Report migration

Consumers must accept `schema_version: "0.8"`, the three new default view IDs,
package node IDs and the new nullable `dependencies` field in every view.
Existing finding envelopes, source inventory and coverage policies are retained.

`ViewReport.dependencies` contains `ReportDependency` values with `source` and
`target` view-node IDs and a tuple of readable `EvidenceLocation` values. Evidence
includes original source endpoints, fact IDs, source paths, positions, statements,
resolution kinds and import context. It uses the existing evidence conversion.

- `null` means complete dependency reporting was not requested for that view.
- `[]` means it was requested and there are no inter-node relationships.
- A nonempty array contains every reported relationship, including acyclic ones.

Default package views always request these dependencies. Default source views
leave the field `null`. Cycle detail (`summary` or `component-edges`) controls
cycle findings independently; it does not limit the view-level dependency list.

## Extension compatibility

`GraphViewStrategy.transform(snapshot)` and `CheckStrategy.evaluate(context)`
retain their existing contracts. `PackageView(source_view, max_depth=None)` from
`pyarchgraph.domain.strategies` is a reusable immutable projection. The application
configures package strategies per analysis: a supplied request depth overrides
constructor configuration for that run, while omission preserves it. Stored
registrations and other custom strategies are not mutated. Reserved package view
IDs require their corresponding source import filters.

`ViewRegistration.report_dependencies` defaults to false. Set it to true to
request complete readable dependencies for any registered view. It does not
change which checks run or how findings determine the exit status.

`AnalysisSnapshot.resolved_dependencies` is an optional appended field retaining
original resolver dependencies alongside selected module dependencies. Existing
snapshot constructors can omit it, and module strategies continue using selected
dependencies. Package projection uses the additional collection only to restore
exact-base evidence for a retained import fact whose probable submodule already
maps to the same target package. It preserves original certainty without adding
an unrelated relationship. Normalization and validation cover both collections.

See the [architecture guide](architecture.md) and the [README](../README.md) for
current defining-module imports and complete examples. Historical release and
verification records retain their original versions and observations.
