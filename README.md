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
- `dependency-dag.md`, a Mermaid `flowchart LR` derived only from that model's
  condensation DAG.

Exit status `0` means every non-excluded candidate received an unambiguous
module identity and was successfully read, decoded, and parsed.

Status `1` means partial outputs were written with one or more error diagnostics (for
example, one file could not be parsed). Warnings and structured unresolved
imports do not make an analysis incomplete.

Status `2` means invalid CLI configuration or an output failure.
