# pyarchgraph

## Install and run

Python 3.11 or newer is required, except for CPython 3.14.1. That patch release
contains a `dataclasses` regression that prevents NetworkX from importing; use
Python 3.14.2 or newer within the 3.14 series. NetworkX is the sole runtime
dependency.

Run the tool directly from its GitHub repository without cloning or installing
it into the current project:

```console
uvx --python '>=3.11,!=3.14.1' --from git+https://github.com/danballance/pyarchgraph pyarchgraph SOURCE_ROOT --output-dir build/pyarchgraph
```

The explicit Python constraint prevents `uvx` from selecting CPython 3.14.1
from an existing installation or tool cache. You can instead request a known
fixed interpreter exactly:

```console
uvx --python 3.14.2 --from git+https://github.com/danballance/pyarchgraph pyarchgraph SOURCE_ROOT --output-dir build/pyarchgraph
```

For local development from a clone:

```console
uv sync
uv run pyarchgraph SOURCE_ROOT --output-dir build/pyarchgraph
```

For a flat layout, `SOURCE_ROOT` is normally the repository root. For a `src`
layout, pass `repository/src`. Optional exclusions are OR-combined and may be
repeated:

```console
uv run pyarchgraph . --exclude 'pkg/generated/**' --exclude 'tests/**'
```

The command writes:

- `dependency-graph.json`, the canonical evidence-rich model and architecture score; and
- `dependency-dag.md`, a Mermaid `flowchart TD` derived only from that model's
  condensation DAG, with one `subgraph` per dependency-first layer so an edge
  always points down the page, preceded by the architecture score and breakdown.

Requested artifacts are staged before publication. Replacement is atomic for
each file, not for the JSON/Markdown pair: if publishing the second file fails,
new JSON can remain beside older Markdown. Use `--json-only` for machine
consumers that need one publication point. A staging failure cleans up the new
temporary files and leaves previously published outputs unchanged.

## Architecture score

Every run reports an **experimental 0–100 architecture score**, where higher
means fewer modules involved in cycles and less dependency reach under the
fixed `architecture-v1` heuristic. It appears in the CLI summary, in the JSON
`quality` object, and above the diagram in Markdown. No extra flag is needed.

The score uses the structural module graph (`architecture_dependencies`),
regardless of package depth or whether diagram edges are dotted, solid or omitted. Each distinct internal
module dependency counts once, including self-imports; repeated import
statements do not add weight. Existing type-only, local and probable-submodule
dependencies are included by default. Literal dynamic targets are uncertain
candidates; external and unresolved imports create no internal edges. Raw
syntactic observations remain available separately in `dependencies`.

The formula is:

```text
cycle_fraction = cyclic_modules / active_modules
reach_fraction = reachable_pairs / (active_modules × (active_modules − 1))
score = 100 × [0.70 × (1 − cycle_fraction) + 0.30 × (1 − reach_fraction)]
```

An active module has at least one incoming or outgoing internal dependency.
A cyclic module belongs to a strongly connected component containing several
modules, or imports itself. Reachable pairs are ordered pairs of distinct
modules connected by one or more dependencies. Multiple paths to the same
module count once. Isolated modules are reported but excluded from the
denominators, so adding unused files cannot improve the score. With no active
modules, both fractions are zero; with fewer than two, reach fraction is zero.

The breakdown includes both component scores, total/active/isolated module
counts, internal dependency count, cyclic component and module counts, largest
cyclic component size, reachable-pair count, and maximum fan-in and fan-out. Fan-in and
fan-out count distinct incoming and outgoing edges, including self-imports.
JSON and Python retain full calculation precision; human-readable scores are
rounded to one decimal place. Small changes can therefore be visible in JSON
before the displayed score changes.

For a nonempty, complete inventory with no internal dependencies, the score is
100: this means no observed internal coupling. An empty inventory produces
`score: null` and `unavailable_reason: "no_modules"`. An incomplete analysis
produces `score: null` and `unavailable_reason: "incomplete_analysis"`, with
partial metrics retained. Both component scores are also null in these cases.
Unresolved import records and dynamic-import warnings are counted beside the
score; they can leave dependencies unobserved even when source parsing was
complete. An invalid source scope produces `score: null` and `invalid_scope`, even when
parsing is complete. Other warning-only analyses retain a score.

Every acyclic graph scores at least 85. A four-module chain and a fully
ordered four-module DAG both score 85 despite different direct dependencies.
Adding 49 unrelated directed pairs raises an unchanged two-module cycle from
0 to 98.4455. Removing those unrelated dependencies lowers its score again.
These are formula limitations, not refactoring recommendations. **Do not use
a score threshold or a non-decreasing score as a correctness gate.** Use the
structural findings and explicit dependency rules below.

The Python API exposes the same frozen report:

```python
from pathlib import Path
from pyarchgraph import ArchitectureQuality, analyse

quality: ArchitectureQuality = analyse(Path("src")).quality
print(quality.score)
print(quality.metrics.cyclic_module_count)
print(quality.metrics.reachable_pair_count)
```

Package version `0.3.1` emits JSON schema `0.3`. This schema includes
`architecture_dependencies`, `findings`, `limitations`, `check`, and analysis
provenance. The metric is now `largest_cyclic_component_size`: `a ↔ b ↔ c`
has a three-module SCC but no three-module simple cycle. The deprecated Python
property `largest_cycle_size` remains an alias; JSON uses only the precise name.
The arithmetic formula remains `architecture-v1`; structural graph selection
is independently versioned as `structural-v1`.
Version 0.3.1 changes import extraction and failure handling without changing
either identifier. See the [release implementation notes](docs/release-0.3.1.md).

The dependency-reach signal is informed by
[propagation-cost research](https://www.hbs.edu/ris/download.aspx?name=13-093.pdf).
The normalization over active modules and the 70/30 weighting are specific to
this experimental heuristic. A future formula change must use a new formula
identifier so that scores from different formulas are distinguishable.

## Making a large graph readable

A module-level diagram of a well-layered codebase is hard to read for the same
reason the codebase is well layered: a shared foundation module is imported
directly from every layer above it, so most edges span most of the drawing.
Two options address that without changing what is recorded.

`--view package` projects modules onto a package prefix before condensing.
`--package-depth` sets how many dotted segments to keep (default `2`, which
usually gives one node per architectural area; `1` groups by top-level
package). The projection runs through the same strongly-connected-component
pass as the module view, because grouping can create a cycle the module graph
does not have. Each package edge is labelled with the number of module imports
behind it.

```console
uv run pyarchgraph SOURCE_ROOT --view package
```

`--implied-edges` controls how the diagram draws an edge that a longer path
already implies -- `dotted` (default), `solid`, or `omit`.

The default hides nothing. It draws every edge and dots the implied ones, so
the essential skeleton stays legible while the real coupling is still on the
page. `omit` is the transitive reduction proper: reachability is unchanged, but
be careful reading it as a picture of coupling. The reduction is weight-blind,
dropping an edge whenever any other path reaches the same target regardless of
how many imports it stands for -- and on a layered codebase the implied edges
are usually the heaviest, because a foundation package is reached both directly
and through every layer above it. On one real 28-edge graph the 13 omitted
edges carried 76% of the module imports, including `infrastructure -> kernel`,
justified away by a path through the HTTP layer.

```console
uv run pyarchgraph SOURCE_ROOT --view package --implied-edges omit
```

Both settings affect only the condensation DAG and the diagram. `modules`,
`import_facts` and `dependencies` are always reported at module grain, so the
evidence does not change with the view.

## Structural findings and scope

Start with the [example corpus](examples/README.md): 25 independent projects
cover good patterns, failures, warning cases and every reviewed counterexample.
The manifest states intent and expected behavior; a separate immutable baseline
records what the reviewed commit observed before these fixes.

For a production check with a `src` layout:

```console
uv run pyarchgraph repository/src --project-root repository --expect-package pkg --json-only --check --output-dir build/check
```

`--project-root` records the source root relative to its project (`src` here).
Without it, the explicit source directory is treated as the project and recorded
as `.`. Repeat `--expect-package` to guard expected modules or namespace packages.
Missing expected names or recognizable `src.pkg`/`pkg` mismatches invalidate the
scope and suppress the headline score. This is a limited static guard; passing
no expected names cannot prove that the configured root is correct.

Directories named `tests`, `test_*.py`, and `*_test.py` are excluded by default.
Use `--include-tests` when that scope is deliberate. Effective exclusions are
recorded. Typing-only and local imports remain structural dependencies by
default. `--exclude-type-only` and `--exclude-local` select different evidence
policies; the latter excludes imports in function and class bodies under the
existing scope model, even though a class body can execute eagerly. An edge
survives whenever any included import still supports it.
Moving an import into a function does not automatically improve architecture.

These filters change the structural graph, while coverage checks still use raw
observations. A missing target or recognized dynamic import requires review even
when all its evidence is typing-only or local and that evidence is excluded.
`dependency_resolution_complete` does not become true merely because those
observations were excluded from the selected graph.

Source candidates must be regular files. File symlinks are accepted when their
targets are regular files; directory symlinks are skipped. A discovered `.py`
FIFO, device or other unsupported file type produces `source_not_regular` and
incomplete analysis rather than being opened or silently ignored. Disappearing
or unreadable files produce explicit errors. The regular-file checks do not
guarantee protection against a file being replaced between checking and opening.

Package relationships use an explicit structural policy:

- Raw `dependencies` preserve exact package-base and probable child observations.
- When the same import fact supports an immediate child candidate, its redundant
  package-base evidence is suppressed only in `architecture_dependencies`.
  Thus `from . import b` and `import pkg.b as b` have the same structural targets.
- Attribute imports, wildcard imports, explicit package imports, and re-exports
  without a matching child retain their package relationship. Independent evidence
  keeps a package edge even when another fact's base relationship is suppressed.
- A probable child remains uncertain: an initializer may bind that name to an
  attribute. This policy describes possible structural coupling, not Python's
  runtime initializer execution. Initializer side effects and arbitrary re-export
  behavior are not inferred.

The score and diagram use the selected structural graph. Findings analyze it at
module grain regardless of diagram grouping. Each cyclic SCC has one deterministic
cycle witness with exact import locations and evidence kinds. A witness containing
only exact relationships is `definite`; a cycle requiring probable or dynamic
relationships is `possible`. If a component contains both, its definite witness
and the subset of definite cyclic members are reported. Witness size is bounded by
the component's members; simple cycles are never exhaustively enumerated.

Direct dependency rules catch shortcuts that reachability cannot detect:

```console
uv run pyarchgraph src --project-root . --expect-package app --forbid 'app.presentation*:app.storage*' --json-only --check
```

`--forbid SOURCE:TARGET` matches dotted module IDs using case-sensitive glob
patterns. Repeat it for additional directional rules; duplicate rules and rule
ordering have no semantic effect. Definite forbidden edges
fail; uncertain matches require review. A high score never clears a violation.

`complete` describes inventory, reading and supported source-analysis success.
Parsing or traversal recursion exhaustion produces `source_analysis_limit`;
partial facts from that file are discarded while other files are still analysed.
The CLI writes a valid incomplete report, returns exit 1 and leaves the headline
score null. Unsupported annotation scopes also make analysis incomplete.
`scope_valid` and `dependency_resolution_complete` are separate. Missing internal targets and
relative escapes need review; a valid unmodelled namespace base alone does not.
Recognized dynamic calls (including straightforward importlib aliases) always
warn. Supported literal targets are retained as uncertain evidence.

Alias analysis tracks direct imports and simple name assignments. Alternative
branches use independent environments; joins and loop iterations retain possible
loader aliases. An import is typing-only only when the guard is definitely the
recognized `TYPE_CHECKING` sentinel across the relevant paths. Captures, local
bindings and comprehension iteration targets shadow outer aliases; assignment
expressions in comprehensions can affect the containing scope. Generator writes
remain possible deferred effects, including when a generator is never consumed.

Function bodies use conservative summaries of free bindings, including later
declarations, closure bindings and writes through `global` or `nonlocal`.
Decorators and defaults retain their containing-scope evaluation; methods and
nested class bodies skip enclosing class attributes when resolving free names.
Method annotations can see their class namespace. On Python 3.12 and newer,
generic type parameters shadow aliases in their relevant scopes without leaking
into the containing namespace.

Modern type aliases (`type Name = ...`) are supported, including generic aliases,
bounds and constraints, and type-parameter defaults. Their lazy expressions use
conservative binding summaries that include later declarations and rebindings.
Direct alias expressions can see enclosing class attributes; nested lambdas and
comprehension bodies follow their own scope rules. Recognized dynamic imports in
these expressions retain warnings and uncertain evidence. A type alias alone
does not make an import typing-only; the recognized `TYPE_CHECKING` guard rules
still apply. Direct alias expressions retain their containing module/local
classification; nested lambdas and comprehension bodies are local. Ordinary type
references do not create import edges.

Source syntax must be supported by the Python interpreter running PyArchGraph:
type aliases require Python 3.12 or newer, and type-parameter defaults require
Python 3.13 or newer. Python 3.11 remains supported for older source syntax.
Bounds and defaults on generic functions and classes remain unsupported and
produce the error diagnostic `unsupported_annotation_scope`.

This is a static approximation, not execution or symbolic evaluation. It can
retain a loader that a particular runtime path never calls. Arbitrary attribute
mutation, computed loader names, metaprogramming and general runtime aliasing
remain outside the model; zero warnings does not prove there are no dynamic
imports.

Without `--check`, exit statuses remain 0 for complete analysis, 1 for incomplete
analysis, and 2 for invalid configuration/output errors. With `--check`, status
3 means a definite cycle or forbidden edge, and 4 means review is needed because
of uncertain findings, dependency resolution, empty inventory or invalid scope.
Incomplete analysis still returns 1. The JSON `check.status` is `pass`, `fail`,
or `needs_review`; even `pass` is scoped to the static model and configured rules.

## Baseline comparisons

```console
uv run pyarchgraph src --project-root . --expect-package app --json-only --output-dir build/before
# Make a code change; retain the analysis settings.
uv run pyarchgraph src --project-root . --expect-package app --json-only --check --baseline build/before/dependency-graph.json --output-dir build/after
```

Provenance records the analyser version, available commit, analyser source digest
(including uncommitted changes), interpreter, formula and graph-policy versions,
relative source root, exclusions, expected packages, collector identity, and rules.
Incompatible baselines are rejected before output replacement. Module additions,
removals or path changes require explicit `--allow-inventory-change` after review;
the report lists them. Old schema baselines must be regenerated, not silently
compared under new import semantics. Version 0.3.1 keeps schema `0.3`, but its
changed analyser identity and extraction semantics require regenerating 0.3.0
baselines. Repeated or reordered equivalent forbidden-rule sets produce the same
report and remain baseline-compatible within the same analyser environment.

Comparisons use semantic `(source, target)` dependency identities, so blank lines
and import formatting do not create new violations. Source locations remain
explanatory evidence. JSON witnesses mark newly added dependencies, and the
comparison reports new/resolved definite cyclic relationships, including certainty
promotions. `--check` checks all current findings; a baseline does not grandfather
existing violations or replace that check with a score delta.

`--json-only` skips Mermaid rendering and transitive reduction while retaining
full-project discovery, extraction, resolution, scoring and findings. Existing
unrequested diagram files are left untouched. Analysing changed files alone
would miss cross-file cycles.

## Development checks

```console
uv run pytest
uv run python examples/evaluate.py
uv run python tests/benchmark_quality.py --case layered --modules 10000
uv run python tests/benchmark_quality.py --case pairs --modules 40000
uv run python tests/benchmark_discovery.py --compare-quadratic
uv run python tests/benchmark_pipeline.py --modules 2000 --repeats 3
```

The quality benchmark measures scoring only. The pipeline benchmark launches
the actual CLI and measures startup through artifact writing; add `--with-diagram`
for the diagram route and `--compare-quadratic` to time the reviewed prefix helper.
See [earlier pipeline measurements](docs/performance.md) and the
[0.3.1 implementation measurements](docs/release-0.3.1.md). The discovery
benchmark isolates ambiguity detection from filesystem I/O and inventory
construction. Its optional quadratic reference measures only the old prefix
scan, rather than the whole old helper.

Scoring uses integer bitsets over the component DAG, avoiding a Python object
for every reachable pair. Bit positions and retained masks are local to each
weakly connected component; disconnected pairs therefore avoid large global
bit positions. The score still uses global active-module denominators.
Worst-case bit storage remains quadratic within a connected graph.
Run `--case chain` and `--case cycle` separately to cover long
dependency paths and a single large cyclic component. Memory output is peak
process memory, including input objects, Python and NetworkX. Timing is
reported for investigation rather than used as a flaky unit-test threshold.
