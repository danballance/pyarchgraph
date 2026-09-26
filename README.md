# pyarchgraph

Check a Python project's explicit import statements for cycles, forbidden
dependencies, and unresolved internal imports. Project code is parsed, never
executed.

## Run

Python 3.11 or newer is required, except CPython 3.14.1 because of its
`dataclasses` regression affecting NetworkX. Use an interpreter that supports
the source syntax being checked. NetworkX is the only runtime dependency.

From a clone:

```console
uv sync
uv run pyarchgraph . --exclude examples --exclude docs
```

Or run from Git:

```console
uvx --python '>=3.11,!=3.14.1' --from git+https://github.com/danballance/pyarchgraph pyarchgraph SOURCE_ROOT
```

The source root is the directory normally placed on `sys.path`. Pass the
repository root for a flat layout, or `src` for a source layout. Passing the
package directory itself is not supported. A recognizable `src.pkg`/`pkg`
mismatch is an error; the tool does not discover or correct the root for you.

There are two options:

```console
uv run pyarchgraph src --exclude 'app/generated/**' --forbid 'app.presentation*:app.storage*'
```

- `--exclude GLOB` excludes a POSIX-relative file or directory pattern. Repeat
  it to combine exclusions. Matching uses `pathlib.PurePosixPath.match`.
- `--forbid SOURCE:TARGET` forbids a direct import between case-sensitive dotted
  module-name globs. Repeat it to add rules. Overlapping rules produce one
  finding per dependency, listing every matching rule.

Directories named `tests`, files named `test_*.py` or `*_test.py`, and the usual
`.git`, `.venv`, `venv`, `__pycache__`, `build`, and `dist` directories are always
excluded. Imports inside functions, classes, conditional branches, and
`TYPE_CHECKING` blocks always count as structural dependencies.

## Results

Completed analysis writes one JSON object to stdout. A clean example is:

```json
{
  "dependency_count": 3,
  "findings": [],
  "module_count": 4,
  "schema_version": "0.5"
}
```

| Exit | Meaning | Output |
| --- | --- | --- |
| `0` | Complete analysis with no blocking findings | JSON on stdout |
| `1` | Complete analysis with blocking findings | JSON on stdout |
| `2` | Invalid configuration or incomplete/invalid source analysis | Explanation on stderr; no JSON |

An empty inventory, ambiguous module names, unreadable or unparseable source,
and an incorrect source root are errors. Partial findings cannot turn an
analysis error into a completed report.

The command creates no files. To save a report, redirect stdout:

```console
uv run pyarchgraph src > dependency-check.json
```

Every entry in `findings` prevents a pass:

- `cycle`: one finding per cyclic strongly connected component, including
  self-imports. It lists the component's members, the subset participating in
  definite cycles, and one bounded cycle witness. A definite witness takes
  precedence; a possible witness retains its uncertainty. The component size
  is not a claim about the length of a simple cycle.
- `forbidden_dependency`: a definite or possible dependency matching a boundary
  rule, with the matched rules and import evidence.
- `unresolved_import`: a missing internal target or an escaping relative import.

Evidence includes the original import text where available, a path relative to
the command's working directory, one-based line and column numbers, and the
resolution kind. The source-root prefix is included for `src` layouts.
Repeated imports do not increase the distinct dependency count. Findings and
source evidence have deterministic ordering.

## Import analysis

The analyzer parses each discovered Python module and collects every `import`
and `from ... import ...` statement throughout its syntax tree. Statements count
whether or not their containing code executes. There is one statement-based
analysis mode.

Absolute and relative submodule spellings produce the same structural targets.
For `from package import child`, a source-backed child takes precedence over
that statement's redundant package-base relationship. The child remains a
probable relationship because an initializer could bind an attribute with the
same name. Independent package imports and legitimate re-exports retain their
package dependencies.

Harmless probable relationships do not prevent a pass. A possible cycle or
forbidden dependency does. Valid namespace-package bases and external imports
are not missing internal targets. Unknown top-level names are treated as
external; the tool cannot distinguish every misspelled import from an external
package without additional knowledge.

Calls to `importlib.import_module()`, `__import__()`, and aliases of these
functions produce no dependencies or findings. Surrounding explicit import
statements still count. Dependencies introduced solely through dynamic imports
are outside pyarchgraph's coverage; a passing report does not establish their
absence.

Annotations, generic bounds, type parameter defaults, and type alias expressions
are not interpreted. For example, `def validate[ModelT: BaseModel](...)` does not
prevent analysis: the explicit import of `BaseModel` already represents its
module dependency. The running interpreter must support the source syntax;
invalid or unsupported Python syntax still prevents analysis.

## Python API

```python
from pathlib import Path
from pyarchgraph import AnalysisError, AnalysisReport, analyse, render_json

try:
    report: AnalysisReport = analyse(
        Path("src"),
        excludes=("app/generated/**",),
        forbidden_dependencies=(("app.presentation*", "app.storage*"),),
    )
except AnalysisError as error:
    print(error)
else:
    print(render_json(report), end="")
```

The frozen report exposes the same fields as JSON. Invalid option values raise
`ValueError`; invalid or incomplete analysis raises `AnalysisError`, a
`ValueError` subclass. There is no score, diagram, baseline, output selection,
or configurable evidence policy in version 0.5.1.

## Verification

```console
uv run pytest tests/test_examples.py -q
uv run python -m examples.evaluate
uv run pytest -q
```

The [example corpus](examples/README.md) covers 25 projects and 28 runs. The
manifest explicitly expects nine passes, 18 findings reports, and one analysis
error. The evaluator runs the real CLI and fails if any expectation differs.
Fixture applications are never imported or executed. Historical reviewed
measurements remain archival; their obsolete scores do not specify current
behavior.

Checksmith consumes the JSON directly through its normal subprocess adapter.
Its integration suite also runs all 28 examples through that adapter. Version
0.5.1 limits analysis to explicit import statements and retains JSON schema
`0.5` and the existing exit codes. Update the pinned pyarchgraph revision in
Checksmith and consuming configurations together. The invocation is simply
`pyarchgraph .` (or `pyarchgraph src`), plus any exclusions and boundary rules.
