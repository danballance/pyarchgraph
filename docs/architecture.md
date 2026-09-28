# pyarchgraph architecture

pyarchgraph uses the [shared Python hexagonal guide](hexagonal-architecture-guide.md).
Its domain describes source identities, explicit imports, resolution, graph views
and findings. The application coordinates analysis. Adapters perform filesystem
access, Python parsing, NetworkX operations and CLI presentation.

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
    │   └── rendering.py        # JSON schema 0.7 presentation
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
analysis snapshot. Domain strategies operate on normalized snapshots through
domain-owned resolver, graph-algorithm and strategy protocols. Contextual
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

## Python API migration

This restructuring changes Python import paths and invocation shape. It does not
change JSON schema 0.7, analysis semantics, fact IDs or CLI behaviour. There are
no compatibility wrappers or aggregate public re-export modules.

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

Checksmith integration and its earlier schema 0.6 → 0.7 migration remain a
separate task in that repository. No Checksmith changes or release publishing
are part of this restructuring.

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
