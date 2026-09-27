# pyarchgraph

Check a Python project's explicit import dependencies for cycles and missing
internal targets. Source is parsed, never executed. Reports describe the selected
source scope and its coverage; they do not certify runtime import safety.

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

The default composition reports three views:

| CLI gate | JSON view | Import sites included |
| --- | --- | --- |
| `structural` (default) | `structural` | All explicit import statements |
| `non-typing` | `non-typing` | Excludes recognized `TYPE_CHECKING` bodies |
| `module-body` | `module-body` | Also excludes sites inside any function/method; top-level class bodies remain |

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
implicitly. Roots, exclusions, gate and detail are CLI/API options, not TOML keys.

Acknowledgement remains visible in the report. It does not mark an implementation
as analyzed, create graph edges through it, suppress real source dependencies,
or excuse parse failures or competing bindings. Unused acknowledgements produce
warnings. Missing internal targets remain findings, distinct from coverage errors.

## JSON schema 0.7

The envelope contains `schema_version`, `status`, `gate`, `sources`, `coverage`
and `views`. Source IDs are `source:` followed by a base-directory-relative
POSIX path. View nodes reference source memberships; `sources` provides paths,
import names, binding status and analysis status. With the built-in views each
node corresponds to one source.

Each view contains nodes, enabled check IDs, dependency, cyclic-node and
cyclic-dependency counts, and registered finding envelopes. A cycle finding
represents one cyclic strongly connected component,
including a self-loop, with its members, definite members and one deterministic
witness. `--details component-edges` additionally includes every internal
component dependency. The command never enumerates all elementary cycles.

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
from pyarchgraph import AnalysisOptions, ApplicationFactory, JsonReportRenderer

analyzer = ApplicationFactory().create_analyzer()
report = analyzer.analyse(
    (Path("."), Path("src")),
    options=AnalysisOptions(excludes=("examples",), gate="structural"),
)
print(JsonReportRenderer().render(report), end="")
print(report.status, report.exit_code)
```

`AnalysisOptions` also accepts `details`, `owned_prefixes` and a tuple of frozen
`TargetDeclaration` values. Invalid options raise `ValueError`; invalid roots
raise `AnalysisError`. Source-analysis failures return an incomplete report.
Reports and option/domain objects are frozen dataclasses. The report's
`selected_view` and `exit_code` properties are conveniences, not serialized fields.

Version 0.7 replaces the functional Python API and JSON schema 0.6. Views are an
immutable mapping keyed by exact IDs, such as `report.views["module-body"]`.
Each view contains projected nodes, enabled check IDs, dependency counts,
`cyclic_node_count`, and findings wrapped as `check_id`, `severity`, and `finding`.
Only error findings in the selected view return exit 1; incomplete coverage
always returns exit 2. `base_dir` resolves relative roots and controls report paths.

[Release and migration notes](docs/release-0.7.0.md) describe the breaking changes.
Checksmith's strict schema 0.6 adapter needs a separate migration before the
coordinated release.

## Extending analysis

Runtime code follows ports and adapters: immutable models, resolution policies,
and graph strategy contracts live in `domain`; orchestration and immutable
registries live in `application`; filesystem, AST, TOML, NetworkX, CLI, and JSON
implementations live in `adapters`. `ApplicationFactory` wires those parts.

Supply Python objects implementing `GraphViewStrategy.transform(snapshot)` or
`CheckStrategy.evaluate(context)` to the factory registry. Both use structural
protocols, so subclassing and a dependency injection framework are unnecessary.
Return the public frozen graph and finding dataclasses with immutable tuples;
extra-field subclasses are rejected by output validation.
Each view receives the same normalized immutable snapshot. View validation
allows filtering, renaming, and aggregation while requiring original evidence
and disjoint source memberships. Aggregated cycles describe projected nodes.

See the [external strategy example](examples/custom_strategies.py)
for a package grouping view and advisory check. Exact per-view check selection,
including an empty tuple, works for built-in and custom views. Coverage remains
mandatory. Extension failures raise `ExtensionError`; the CLI prints the error
and returns 2 without a report.

## Verification

The [0.7 verification record](docs/verification-0.7.md) includes the supported
Python matrix, packaging checks, corpus results, and reproducible benchmarks.

```console
uv run pytest -q
uv run python -m examples.evaluate
```

The [example corpus](examples/README.md) has 31 projects and 40 configured runs,
including all three gates, multiple roots, partial analysis and acknowledged
boundaries. Examples are parsed and never executed. Original research and its
source hashes remain archival. Collection benchmarks and fresh campaign replay
scripts live in `benchmarks/`; they do not overwrite the original research.
