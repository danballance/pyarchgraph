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

## Architecture score

Every run reports an **experimental 0–100 architecture score**, where higher
means fewer modules involved in cycles and less dependency reach under the
fixed `architecture-v1` heuristic. It appears in the CLI summary, in the JSON
`quality` object, and above the diagram in Markdown. No extra flag is needed.

The score always uses the raw module graph, regardless of package depth or
whether diagram edges are dotted, solid or omitted. Each distinct internal
module dependency counts once, including self-imports; repeated import
statements do not add weight. Existing type-only, local and probable-submodule
dependencies are included. External and unresolved imports create no internal
edges.

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
cycle size, reachable-pair count, and maximum fan-in and fan-out. Fan-in and
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
complete. Warning-only analyses retain a score and the existing exit status.

Use this as a signal when comparing changes to the same codebase with the same
source root, exclusions and analyser/formula versions. It is not an overall
code-quality grade, a prediction of runtime failures, or a calibrated way to
rank unrelated projects. Adding functionality or splitting/merging modules
can change the score; read the counts alongside it. Removing a redundant
direct dependency can leave the headline unchanged when cycles and
reachability stay the same. Architectural rules, configurable weights,
automated baseline comparisons and CI regression gates are not part of this
version.

For a manual comparison, keep each run in a separate output directory:

```console
uv run pyarchgraph SOURCE_ROOT --output-dir build/before
# Make the code change, keeping the analysis settings identical.
uv run pyarchgraph SOURCE_ROOT --output-dir build/after
```

The Python API exposes the same frozen report:

```python
from pathlib import Path
from pyarchgraph import ArchitectureQuality, analyse

quality: ArchitectureQuality = analyse(Path("src")).quality
print(quality.score)
print(quality.metrics.cyclic_module_count)
print(quality.metrics.reachable_pair_count)
```

Package version `0.2.0` uses JSON schema `0.2` in both diagram views, adding the
top-level `quality` object. Consumers that check the exact schema must accept
this addition. Code constructing `AnalysisResult` directly must now supply its
`quality` report; normal `analyse()` calls populate it automatically.
`ArchitectureQuality` and `ArchitectureMetrics` are exported from `pyarchgraph`.

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

The `analysis` block records `excludes`, `view` and `package_depth`, so a
committed artifact states what it covered -- without them a reader cannot tell
an excluded package from an absent one. The source root is deliberately not
recorded: every path in the document is relative to it, which is what lets an
artifact be compared between machines.

Exit status `0` means every non-excluded candidate received an unambiguous
module identity and was successfully read, decoded, and parsed.

Status `1` means partial outputs were written with one or more error diagnostics (for
example, one file could not be parsed). Warnings and structured unresolved
imports do not make an analysis incomplete.

Status `2` means invalid CLI configuration or an output failure.

## Development checks

```console
uv run pytest
uv run python tests/benchmark_quality.py --case layered --modules 10000
```

The manual benchmark measures scoring only, excluding parsing and diagram
rendering. It uses integer bitsets over the component DAG, avoiding a Python
object for every reachable pair. Worst-case bit storage is still quadratic in
module count. Run `--case chain` and `--case cycle` separately to cover long
dependency paths and a single large cyclic component. Memory output is peak
process memory, including input objects, Python and NetworkX. Timing is
reported for investigation rather than used as a flaky unit-test threshold.
