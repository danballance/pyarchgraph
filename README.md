# pyarchgraph

Check a Python project's explicit import dependencies for cycles and missing
internal targets. Source is parsed, never executed. Reports describe the selected
source scope and its coverage; they do not certify runtime import safety.

## Documentation

Browse the [documentation site](https://danballance.github.io/pyarchgraph/) for
the domain, application and adapter guides, plus the terminology glossary.
You can also open [the documentation index](docs/index.html) locally in a browser.

The `Publish documentation` workflow publishes the HTML guides on every push to
`main`, including merged pull requests, and can be run manually from the Actions
tab. To enable it, select **GitHub Actions** under the repository's
**Settings → Pages → Build and deployment → Source**. No extra secrets are needed.
The workflow stages only `docs/*.html`; links to source files and Markdown open
on GitHub at the deployed commit, while guide navigation stays within the site.

## Run

Python 3.11 or newer is required, except CPython 3.14.1. Use an interpreter that
supports the source syntax being checked. NetworkX is the only runtime dependency.

```console
uv sync
uv run pyarchgraph . --exclude examples --exclude docs
uv run pyarchgraph src
uv run pyarchgraph . src --exclude backend
```

Each root is a directory normally placed on `sys.path`. Multiple explicit roots
are analyzed together. Nested roots take ownership of their own files, so `.`
and `src` do not duplicate source identities. Root order has no precedence;
conflicting bindings across roots are an incomplete analysis. Root diagnostics
suggest corrections but never silently alter the selected scope.

The command writes one JSON object to stdout and creates no report files. Redirect
stdout to save a report. Configuration/argument errors that prevent analysis are
reported on stderr instead.

## Results and views

| Exit | Meaning |
| --- | --- |
| `0` | Complete within declared scope and accepted boundaries; selected view has no error findings |
| `1` | Complete within that scope; selected view has error findings |
| `2` | Incomplete analysis, invalid invocation/configuration, or extension failure |

Source-analysis errors produce **partial JSON**, including recovered findings,
coverage diagnostics and sources that could not be parsed. An incomplete scan
never passes, even if the recovered graph has no cycles.

The default composition reports six views: three source views and three package
views with the same import filters. The gate remains `structural` unless selected
explicitly.

| Source view / gate | Package view / gate | Import sites included |
| --- | --- | --- |
| `structural` (default gate) | `package-structural` | All explicit import statements |
| `non-typing` | `package-non-typing` | Excludes recognized `TYPE_CHECKING` bodies |
| `module-body` | `package-module-body` | Also excludes sites inside any function/method; top-level class bodies remain |

```console
uv run pyarchgraph src --gate non-typing
uv run pyarchgraph src --gate module-body --details component-edges
```

Only the selected gate's error findings determine exit `1`; other views remain
informational. Coverage remains independent of the gate. For example, selecting
`module-body` cannot conceal an unacknowledged stub target mentioned only inside
a typing guard.

`TYPE_CHECKING` recognition is deliberately conservative: simple positive tests
of unambiguously imported module-level typing aliases are recognized; rebinding,
shadowing, compound/negated tests and custom conditions are retained. A function
can run during startup, and a module-body import can be conditional. These views
are syntactic dependency filters, not execution-order simulations.

## Package-level analysis

Package views reuse the collected sources and resolved imports. Each ordinary
module belongs to its immediate containing package; `__init__.py` belongs to its
own package. For example, `acme.orders.service` and `acme.orders`'s initializer
both belong to `package:acme.orders`. Nested packages remain separate, with no
source counted in both a parent and child. Standalone modules and sources without
reliable bindings remain individual source nodes.

```console
uv run pyarchgraph src --gate package-structural
uv run pyarchgraph src --gate package-module-body --package-max-depth 2
```

By default there is no depth cap. `--package-max-depth N` caps the containing
package's dotted name at a positive number of components: depth 1 groups
`acme.orders` and `acme.inventory` into `package:acme`; depth 2 keeps those two
packages separate while grouping `acme.orders.internal` under `acme.orders`.
The option applies to package views; source views keep their original detail.

Imports are filtered before grouping. Dependencies within a resulting node are
removed, dependencies between the same node pair are merged with their evidence,
and isolated nodes remain visible. Package reports include every dependency,
even in an acyclic graph, with the original source paths and import statements.
A package cycle may exist when no individual modules form a cycle: different
modules in each package can establish dependencies in both directions.

Namespace packages work without `__init__.py`. Imports of their source modules
contribute dependencies as usual. An import targeting only a namespace container
has no source target and does not create a package edge. Package views describe
internal source packages, not installed distributions or runtime initialization.

## Scope, ownership and boundaries

All selected `.py` files are inventoried, including numeric migrations,
keyword-named directories, hyphenated scripts and hidden helpers. A source that
cannot be mapped losslessly to a dotted name still contributes outgoing absolute
imports. Relative imports without an established package context are coverage
errors. Ordinary same-root package/module precedence is represented explicitly;
shadowed sources are still parsed.

Regular packages and exact source modules establish internal ownership. Shared
namespace ancestors do not: discovering `google.api_core` does not imply owning
`google.auth`. A missing target inside an owned branch is a finding; unknown
external branches are outside this graph, without a claim that they are installed.

Use `--exclude GLOB` repeatedly for POSIX-relative `PurePosixPath.match` patterns.
Patterns apply within each selected root. Tests (`tests`, `test_*.py`, `*_test.py`)
and the usual `.git`, `.venv`, `venv`, `__pycache__`, `build`, and `dist` directories
are excluded automatically. Reports disclose exclusions and pruned directories;
they do not recursively count files inside excluded trees. File symlinks are
retained under their logical paths; directory symlinks are not traversed.

Stubs, Cython sources and recognizable native artifacts explain a target's
availability but do not reveal its implementation dependencies. Referenced
project-owned targets of this kind keep the result incomplete until explicitly
acknowledged. Generated and otherwise unrecognizable native targets can be
declared in an explicit TOML configuration:

```toml
owned_prefixes = ["acme"]

[[targets]]
name = "acme.engine"
kind = "native"
reason = "Compiled implementation is checked separately"
acknowledged = true

[[targets]]
name = "acme.version"
kind = "generated"
path = "build_config.py"
reason = "Release build generates this module"
acknowledged = true
```

```console
uv run pyarchgraph src --config architecture.toml
```

Supported configured kinds are `stub`, `native` and `generated`. Each target needs
an exact name and a reason. `path` is optional and relative to the configuration
file; `acknowledged` defaults to false. Configuration is never discovered
implicitly. Roots, exclusions, gate, detail and package depth are CLI/API options,
not TOML keys.

Acknowledgement remains visible in the report. It does not mark an implementation
as analyzed, create graph edges through it, suppress real source dependencies,
or excuse parse failures or competing bindings. Unused acknowledgements produce
warnings. Missing internal targets remain findings, distinct from coverage errors.

## JSON schema 0.8

The envelope contains `schema_version`, `status`, `gate`, `sources`, `coverage`
and `views`. Source IDs are `source:` followed by a base-directory-relative
POSIX path. View nodes reference source memberships; `sources` provides paths,
import names, binding status and analysis status. Source views keep one node per
source; package nodes use `package:<dotted-name>` IDs and list their member source
IDs. Standalone or unbound sources keep their `source:` IDs in package views.

Each view contains nodes, enabled check IDs, dependency, cyclic-node and
cyclic-dependency counts, and registered finding envelopes. A cycle finding
represents one cyclic strongly connected component,
including a self-loop, with its members, definite members and one deterministic
witness. `--details component-edges` additionally includes every internal
component dependency. The command never enumerates all elementary cycles.

The view-level `dependencies` field contains every relationship when enabled,
independently of cycle detail. It is always enabled for the default package views.
Each entry has `source` and `target` view-node IDs and readable `evidence`,
including original source endpoints. `null` means relationship reporting was not
requested; `[]` means it was requested and no relationships exist. The default
source views use `null`.

Evidence is deduplicated per dependency, with one-based character positions,
original statement text, resolution kind and execution-context annotations.
`definite` describes resolution certainty, not an import-time crash. Possible
cycles also fail their selected gate; harmless probable edges do not.

The resolver normalizes equivalent absolute/relative submodule spellings.
Independent package imports and legitimate re-exports retain their relationships.
Initializer self-base suppression does not suppress ordinary-module self-imports.
Calls to `importlib.import_module()`, `__import__()` and their aliases remain
outside coverage. External implementation graphs, type-expression evaluation,
build execution and initialization-order proof remain outside scope.

## Python API

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
        options=AnalysisOptions(
            excludes=("examples", "docs", "benchmarks"), gate="structural"
        ),
    )
)
print(JsonReportRenderer().render(report), end="")
print(report.status, CliExitCodePolicy().exit_code(report))
```

`AnalysisOptions` also accepts `package_max_depth` (a positive integer or `None`),
`details`, `owned_prefixes` and a tuple of frozen
`TargetDeclaration` values from `pyarchgraph.domain.models`. Pass options and roots
inside an `AnalysisRequest`; `base_dir` is an optional request field. Invalid
options raise `ValueError`; invalid roots raise `AnalysisError` from
`pyarchgraph.application.exceptions`. Validation runs before external I/O.
Source-analysis failures return an incomplete report. Requests, reports and
option/domain objects are frozen dataclasses. The report's `selected_view`
property is a convenience, not a serialized field. Exit-code policy belongs to
the CLI adapter.

The earlier 0.7 API replaced the functional API and JSON schema 0.6. Views are an
immutable mapping keyed by exact IDs, such as `report.views["module-body"]`.
Each view contains projected nodes, enabled check IDs, dependency counts,
`cyclic_node_count`, and findings wrapped as `check_id`, `severity`, and `finding`.
Only error findings in the selected view return exit 1; incomplete coverage
always returns exit 2. `base_dir` resolves relative roots and controls report paths.

[Historical 0.7 migration notes](docs/release-0.7.0.md) describe the earlier changes.
[Package views and schema 0.8 migration](docs/release-0.8.0.md) describe the current
report additions and new options.
The [architecture and Python API migration guide](docs/architecture.md) describes
the current package layout and explicit imports. Package initializers provide no
API re-exports or compatibility aliases. JSON consumers must accept schema 0.8,
the new view IDs and nullable view dependencies. Checksmith's strict schema 0.6
adapter needs a separate migration before a coordinated release.

## Extending analysis

Start with the terminology glossary ([Markdown](docs/glossary.md) or
[HTML](docs/glossary.html)) and the package guides:
[Domain](docs/domain.html), [Application](docs/application.html), and
[Adapters](docs/adapters.html). Each guide has a short walkthrough and a complete
class index. Open the HTML files in a browser; they work offline without a build step.

Runtime code follows ports and adapters: immutable models, resolution policies,
and graph strategy contracts live in `domain`; use cases, request/result values,
ports and immutable registries live in `application`; CLI, TOML and JSON live in
`adapters.driving.cli`; filesystem, AST and NetworkX live in `adapters.driven`.
`main.ApplicationFactory` wires those parts. The
[shared hexagonal guide](docs/hexagonal-architecture-guide.md) explains the
conventions reused across projects.

Supply Python objects implementing `GraphViewStrategy.transform(snapshot)` or
`CheckStrategy.evaluate(context)` to the factory registry. Built-in implementations
and the supplied examples explicitly inherit their protocols so their contracts
are visible in class declarations. Both protocols still support structural typing:
external implementations can provide the required methods without subclassing.
Return the public frozen graph and finding dataclasses with immutable tuples;
extra-field subclasses are rejected by output validation.
Each view receives the same normalized immutable snapshot. View validation
allows filtering, renaming, and aggregation while requiring original evidence
and disjoint source memberships. Aggregated cycles describe projected nodes.

`PackageView(source_view, max_depth=None)` in `pyarchgraph.domain.strategies`
provides reusable package projection. A request's supplied `package_max_depth`
overrides registered package strategies for that analysis; omission preserves
constructor defaults. The registry creates per-run values without mutating its
stored strategies. `ViewRegistration(..., report_dependencies=True)` enables
complete readable dependency reporting for any view; the default is false.

See the [external strategy example](examples/custom_strategies.py)
for a custom grouping view and advisory check. Exact per-view check selection,
including an empty tuple, works for built-in and custom views. Coverage remains
mandatory. Extension failures raise `ExtensionError`; the CLI prints the error
and returns 2 without a report.

## Verification

The [architecture migration verification record](docs/verification-hexagonal.md)
records this restructuring's checks. The historical
[0.7 verification record](docs/verification-0.7.md) includes the earlier Python
matrix, packaging checks, corpus results, and reproducible benchmarks.

```console
uv run lint-imports
uv run pytest -q
uv run python -m examples.evaluate
```

The [example corpus](examples/README.md) has 31 projects and 40 configured runs,
including all three source gates, multiple roots, partial analysis and acknowledged
boundaries. Examples are parsed and never executed. Original research and its
source hashes remain archival. Collection benchmarks and fresh campaign replay
scripts live in `benchmarks/`; they do not overwrite the original research.
