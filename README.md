# pyarchgraph

## Install and run

Python 3.11 or newer is required. NetworkX is the sole runtime dependency.

Run the tool directly from its GitHub repository without cloning or installing
it into the current project:

```console
uvx --from git+https://github.com/danballance/pyarchgraph pyarchgraph SOURCE_ROOT --output-dir build/pyarchgraph
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
