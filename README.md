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

- `dependency-graph.json`, the canonical evidence-rich model; and
- `dependency-dag.md`, a Mermaid `flowchart TD` derived only from that model's
  condensation DAG, with one `subgraph` per dependency-first layer so an edge
  always points down the page.

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
