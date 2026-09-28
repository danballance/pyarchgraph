# pyarchgraph architecture

pyarchgraph uses the [shared Python hexagonal guide](hexagonal-architecture-guide.md).
Its domain describes source identities, explicit imports, resolution, graph views
and findings. The application coordinates analysis. Adapters perform filesystem
access, Python parsing, NetworkX operations and CLI presentation.

For plain-language definitions, see the glossary in [Markdown](glossary.md) or
[HTML](glossary.html). The standalone
package guides explain how the pieces work together and list every class:
[Domain](domain.html), [Application](application.html), and [Adapters](adapters.html).
Open these HTML files directly in a browser; no build step or internet connection is needed.

## Runtime layout

```text
pyarchgraph/
├── __init__.py                  # Version metadata; no re-exports
├── __main__.py                  # Console and python -m bootstrap
├── main.py                      # ApplicationFactory and concrete wiring
├── domain/
│   ├── models.py                # Values, source identities, facts and findings
│   ├── bindings.py              # Pure source-binding policy
│   ├── targets.py               # Target reconciliation policy
│   ├── validation.py            # Snapshot, view and finding validation
│   └── ...                      # Resolution, canonicalization, coverage, graphs
├── application/
│   ├── requests.py             # AnalysisRequest and AnalysisOptions
│   ├── results.py              # AnalysisReport, Coverage, ViewReport, RegisteredFinding
│   ├── exceptions.py           # AnalysisError and ExtensionError
│   ├── ports/
│   │   ├── analysis.py         # ProjectAnalyzer input port
│   │   ├── sources.py          # Discovery/extraction contracts and observations
│   │   └── project.py          # Project-access contract and location observations
│   ├── use_cases/
│   │   └── analyse_project.py  # AnalyseProject orchestration
│   └── ...                     # Catalog assembly, scope and strategy coordination
└── adapters/
    ├── driving/cli/
    │   ├── application.py      # Arguments, invocation and exit-code policy
    │   ├── configuration.py    # TOML input translation
    │   └── rendering.py        # JSON schema 0.8 presentation
    └── driven/
        ├── filesystem/
        │   ├── discovery.py   # Source inventory and exclusions
        │   └── project.py     # Location and metadata access
        ├── python_ast.py      # Parse source without executing it
        └── networkx_graph.py  # Graph algorithm implementation
```

The domain and application use only the standard library and perform no external
I/O. Within each layer, modules may collaborate. The CLI component's modules may
use one another, as may the filesystem component's modules. CLI, filesystem,
Python AST and NetworkX remain separate adapter components. Only the composition
root connects concrete implementations; adapters do not import it.

Package initializers are inert, and callers import from defining modules. Domain
and application imports work when NetworkX is unavailable. Runtime behaviour is
class-based; `__main__.main` is the one bootstrap function.

## Analysis boundary and data flow

`ProjectAnalyzer.analyse(request: AnalysisRequest) -> AnalysisReport` is the input
contract. `AnalyseProject` implements it. `AnalysisRequest` carries a nonempty
tuple of source roots, frozen `AnalysisOptions`, and an optional `base_dir`.
Omitting options selects the standard defaults. Request and gate validation
complete before discovery, extraction or metadata access.

The use case locates roots through the project port and asks the catalog builder
to discover sources and extract import observations. Extraction returns frozen
`ImportFactDraft` values without IDs. Catalog assembly rebases source identities
and paths into the report's coordinate system, then canonicalizes drafts once
into final `ImportFact` values with nonempty IDs. The established identity fields,
digest algorithm, collision handling, ordering and coordinate conventions are
preserved.

Pure domain policies select source bindings, reconcile target declarations and
resolve imports. The application adds scope diagnostics and prepares the immutable
analysis snapshot, preserving both selected module dependencies and optional
`resolved_dependencies` from the resolver. Domain strategies operate on normalized
snapshots through domain-owned resolver, graph-algorithm and strategy protocols. Contextual
validation verifies view membership, original evidence and findings at extension
boundaries. Built-ins explicitly inherit their protocols; external extensions
may implement them structurally.

The application owns the assembled catalog, reports, coverage summaries and
registered findings. Source and project observations live beside their consuming
ports, independent of report DTOs. Domain `ViewGraph` is an intermediate graph;
application `ViewReport` is a completed view result. `AnalysisError` and
`ExtensionError` describe failures at the application boundary. Reconciliation
failures are translated there without changing user-visible messages.

`AnalysisReport.views` remains immutable and `selected_view` remains available.
Process exit codes belong to the CLI's `CliExitCodePolicy`: complete accepted
analysis returns 0, selected error findings return 1, and incomplete analysis or
invocation/extension failures return 2. Source failures can return useful partial
reports, while invalid invocation and extension failures produce no JSON report.

## Package projection and dependency reports

The six default views share discovery, parsing, resolution, graph algorithms and
checks. `structural`, `non-typing` and `module-body` retain source nodes.
`PackageView` wraps each source filter to provide `package-structural`,
`package-non-typing` and `package-module-body`. Filtering precedes aggregation.

Package membership comes from reliable source bindings: an ordinary module's
containing package, or the initializer's own package. Namespace packages require
no initializer. Membership is disjoint; parent nodes do not also contain child
package sources. Standalone and unbound sources stay as individual nodes.
`AnalysisOptions.package_max_depth` optionally caps dotted package names; depth
1 means their first component. It is validated before project access and applied
to immutable per-run registrations, leaving the reusable registry unchanged.

Projection merges evidence for each node pair, removes dependencies internal to
a node and retains isolated nodes. The normal module dependency policy remains
unchanged. Package views can recover discarded exact-base evidence from
`AnalysisSnapshot.resolved_dependencies` only when the same retained import fact
has probable-child evidence projecting to the same target package. Both evidence
collections are normalized and validated; snapshots without the optional resolver
collection remain supported. This preserves certainty without inventing an edge.

`ViewRegistration.report_dependencies` controls complete dependency reporting.
It defaults to false and is true for the default package views. The engine uses
the existing evidence interpreter to create `ReportDependency` values with
view-node endpoints and readable original source locations. `ViewReport.dependencies`
is `None` when not requested and a tuple, possibly empty, when requested. JSON
schema 0.8 renders these as `null` or an array, independently of cycle detail.

Package cycles concern groups, so an acyclic module graph can have a cyclic
package graph. Namespace-container-only imports have no source target and do not
create package edges. Coverage and gate policy are otherwise shared: the default
gate remains `structural`, and incomplete coverage still takes precedence.

## Python API migration

The earlier restructuring changed Python import paths and invocation shape.
There are no compatibility wrappers or aggregate public re-export modules.
Package analysis preserves those contracts, adds an optional request setting and
snapshot field, and extends reports to JSON schema 0.8. See the
[schema migration note](release-0.8.0.md).

```python
from pathlib import Path

from pyarchgraph.adapters.driving.cli.application import CliExitCodePolicy
from pyarchgraph.adapters.driving.cli.rendering import JsonReportRenderer
from pyarchgraph.application.requests import AnalysisOptions, AnalysisRequest
from pyarchgraph.main import ApplicationFactory

analyzer = ApplicationFactory().create_analyzer()
report = analyzer.analyse(
    AnalysisRequest(
        source_roots=(Path("."),),
        options=AnalysisOptions(excludes=("examples", "docs", "benchmarks")),
    )
)
print(JsonReportRenderer().render(report), end="")
exit_code = CliExitCodePolicy().exit_code(report)
```

Import domain values such as `TargetDeclaration` from `pyarchgraph.domain.models`,
graph types from `pyarchgraph.domain.graph`, and application failures from
`pyarchgraph.application.exceptions`. Replace `AnalysisService` with
`application.use_cases.analyse_project.AnalyseProject` and report `GraphView` with
`application.results.ViewReport`. Move former `analyse(roots, options=...,
base_dir=...)` arguments into one `AnalysisRequest`. Replace `report.exit_code`
with the CLI policy when process-status semantics are needed.

Checksmith integration and its schema migration remain a separate task in that
repository; consumers must now accept schema 0.8. No Checksmith changes or release
publishing are part of package analysis.

## Enforcement and verification

`pyproject.toml` enforces exhaustive inward layers, driving/driven independence,
independence of driven components, and NetworkX isolation. External imports and
`TYPE_CHECKING` imports are included. Recursive pytest checks add core purity,
absolute defining-module imports, inert package initializers and class-based
behaviour. Negative fixtures prove nested violations fail; cold-import tests
block NetworkX and verify that core imports never load adapters.

```console
uv run lint-imports
uv run pytest -q
uv run python -m examples.evaluate
```

Tests are grouped by execution scope under `tests/unit`, `tests/integration`,
`tests/acceptance` and `tests/architecture`. CI runs dependency checks, tests and
the 40-run corpus on Python 3.11–3.14, excluding 3.14.1 through the package
requirement. Installed-package smoke tests verify both entry points outside the
checkout. The [migration verification record](verification-hexagonal.md) records
the observed checks; historical research and release records remain archival.
