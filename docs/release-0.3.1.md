# Version 0.3.1: import accuracy and failure handling

Some supported alias and scope patterns could previously omit dynamic imports
or classify runtime imports as typing-only, allowing an incomplete dependency
graph to pass a check. Version 0.3.1 retains possible loader evidence, requires
definite typing guards before filtering imports, and reports source-analysis
limits explicitly. It also fixes rule-baseline compatibility, discovery scaling
and output staging cleanup.

The implementation starts from reviewed commit
`beb5266202b8a0ca798c4e0a5d9964049eecde0b`. Before changes, the existing suite
passed **248 tests** and the corpus completed **28 configured runs**.

## Extraction and scope changes

- **R1 — Control flow:** independent branch environments prevent one alternative
  from erasing another's alias. Conservative joins preserve possible loaders
  through branches, exception paths, loop iterations and exits, and match
  alternatives. A typing guard can exclude evidence only when its typing meaning
  is definite. For example, a loader shadowed only in the first branch of an
  `if` remains available in the `else`, producing uncertain dynamic evidence and
  `needs_review` for a possible cycle.
- **R2 — Bindings:** a shared binding inventory covers ordinary assignments,
  arguments, imports, exception targets, match captures and generic parameters.
  Comprehension iteration targets remain local, while assignment expressions
  affect the containing scope. Repeated iterations and deferred generator writes
  retain possible effects. On Python 3.12 and newer, generic parameters shadow
  aliases; type-parameter bounds and defaults, and lazy `type` alias expressions,
  produce `unsupported_annotation_scope` and incomplete analysis.
- **R3 — Deferred lookup:** function bodies use summaries of possible free-name
  bindings rather than only the environment at definition. Stable later loader
  declarations are recognized; mutable typing aliases do not prove an import is
  typing-only. Lexical ownership distinguishes locals, closures, `global` and
  `nonlocal`, including mutations by sibling functions. Methods and nested class
  bodies skip outer class attributes; method annotations retain access to their
  class namespace. Decorators and ordinary defaults use the containing scope.

For the reviewed cycle fixtures, runtime imports stay exact and cause `fail`
with typing-only evidence excluded. Recognized literal dynamic targets retain
`dynamic_literal` evidence and a warning, so a cycle requiring one remains
`possible` and causes `needs_review`.

These rules remain a conservative static approximation. They can retain evidence
from paths that do not execute. They do not infer arbitrary attribute mutation,
computed names, runtime aliasing or metaprogramming. Package initializer execution
and arbitrary re-exports remain outside the existing resolution policy.

## Independent fixes

| Item | Result |
| --- | --- |
| R4: repeated or reordered forbidden rules | Validated rules are sorted and deduplicated before findings and provenance consume them. Equivalent rule sets produce identical JSON in the same analyser environment and round-trip through baselines. |
| R5: AST resource limits | Parsing or traversal `RecursionError` produces `source_analysis_limit`. Facts from the affected file are discarded, other files remain available, `complete` is false, the headline score is null and the CLI returns 1 with a valid report. |
| R6: discovery prefix scan | Proper dotted prefixes replace the all-pairs module-ID scan. Duplicate handling, transitive ambiguity groups and deterministic diagnostics are preserved. |
| R7: non-regular sources | Discovered `.py` candidates must target regular files; unsupported types produce `source_not_regular` and incomplete analysis. Regular-file symlinks remain supported and directory symlinks remain skipped. |
| R8: failed output staging | The temporary path is recorded when the file opens, allowing cleanup after write, flush, sync or close failures without hiding the original error. Failure staging either requested artifact leaves published files unchanged. |

The regular-file checks cover the ordinary FIFO case; they do not guarantee
protection against a file being replaced between its path check and open.

## Preserved contracts and optional improvements

**O1:** reachability bit positions are now local to weakly connected components,
and masks are released between components. The reachable-pair count, global
active-module denominators and exact score arithmetic are unchanged.

**O2:** `--exclude-type-only` and `--exclude-local` select structural evidence.
Missing-target and dynamic-import coverage checks still use raw observations,
including excluded imports, and can keep the result at `needs_review`.

**O3:** all requested artifacts are staged before publication, but replacement
is atomic per file. Failure replacing the second artifact can leave new JSON
beside older Markdown. Machine consumers needing a single publication point
should use `--json-only`.

The experimental score remains descriptive and is not a gate. Exact, probable
submodule and dynamic-literal evidence remain distinct. Cycle checks still use
the full module graph; package projection remains a presentation choice. Source
locations and semantic dependency identities remain available for comparisons.

## Version and baseline compatibility

The package version is **0.3.1**. JSON schema **0.3**, score formula
**`architecture-v1`** and graph policy **`structural-v1`** are unchanged. These
repairs require no new serialized evidence fields or resolution kinds.

Extraction results have changed. Regenerate baselines made by version 0.3.0:
analyser version, commit/source digest and interpreter provenance checks remain
in force even when the schema matches. An incompatible baseline is rejected
before replacing outputs. Equivalent repeated/reordered rule sets are compatible
when the remaining analyser identity and settings match.

## Implementation measurements

The O1 measurements below were collected on a shared Linux x86-64 development
machine running CPython 3.14.2. They are observations, not CI thresholds or
portable performance guarantees. The isolated reachability timer excludes graph
construction and SCC enumeration; it includes condensation and counting.

| Modules in disconnected pairs | Reachable pairs | Updated helper elapsed time | Old retained integer-mask bytes | Updated peak retained integer-mask bytes |
| ---: | ---: | ---: | ---: | ---: |
| 10,000 | 5,000 | 0.083 s | 6,929,200 | 56 |
| 20,000 | 10,000 | 0.170 s | 27,191,864 | 56 |
| 40,000 | 20,000 | 0.337 s | 107,718,536 | 56 |

Mask measurements sum `sys.getsizeof` over values retained in the reachability
dictionary. The original dictionary retains every SCC's mask; the replacement
retains only the current weak component's masks. Peak updated mask storage was
observed with line tracing in a separate untimed run. These byte counts exclude
graph objects, dictionaries, positions, inputs and Python/NetworkX overhead.
Whole-process memory is therefore substantially larger: the 40,000-module pair
scoring benchmark observed about **111.0 MiB peak RSS** and **0.718 s** scoring
time. These are separate measurements and must not be read as full-pipeline
memory or elapsed-time results.

The 10,000-module chain retained 49,995,000 reachable pairs and score **85.0**;
the 10,000-module cycle retained 99,990,000 reachable pairs and score **0.0**.
Connected graphs can still require quadratic bit storage.

The discovery benchmark generates flat or dotted namespace inventories outside
the timer. It measures the complete new ambiguity-removal helper. Its optional
quadratic comparison measures only the former prefix scan, a lower bound for
the old helper, and must not be presented as an end-to-end speedup.

| Modules | Updated flat helper median | Old flat prefix scan alone | Updated namespace helper median |
| ---: | ---: | ---: | ---: |
| 1,000 | 0.001318 s | 0.112790 s | 0.002345 s |
| 2,000 | 0.005402 s | 0.563456 s | 0.004806 s |
| 4,000 | 0.007949 s | 2.178461 s | 0.011427 s |
| 8,000 | 0.018145 s | 8.912852 s | 0.026170 s |

Updated helper figures are medians of three runs; the optional old scan is a
single sample at each size. Machine load affects these short measurements. The
flat inventory shows the removal of the former all-pairs scan, without imposing
fragile wall-clock limits in tests.

```console
uv run python tests/benchmark_discovery.py --compare-quadratic
uv run python tests/benchmark_discovery.py --case namespaces
uv run python tests/benchmark_quality.py --case pairs --modules 40000
uv run python tests/benchmark_quality.py --case chain --modules 10000
uv run python tests/benchmark_quality.py --case cycle --modules 10000
uv run python tests/benchmark_pipeline.py --modules 2000 --repeats 3
```

## Verification

The final suite on CPython **3.14.2** passed **364 tests in 6.21 s**, up from
248 baseline tests. All **28 corpus runs** completed with their expected graph
and policy outcomes. `ruff check pyarchgraph tests`, `git diff --check`, frozen
`uv sync` and the offline lock check passed; installed metadata reports 0.3.1.

Focused regressions cover branch and scope facts, selected edges, certainty,
completeness and check outcomes; baseline rule round-trips; injected AST limits;
FIFO and symlink inputs; and staging failures on both requested artifacts.
Quality checks include mixed disconnected chains, diamonds, SCCs and self-loops,
exact global denominators and the existing randomized reachability comparison.
Discovery matches the former algorithm on 200 generated mixed inventories,
including duplicates and overlapping prefix conflicts.

The final 2,000-module layered CLI benchmark retained **9,323 imports and
structural dependencies** and score **85.0**. Three JSON-only runs took
2.495 / 2.372 / 2.301 seconds (median **2.372 s**), with **116.6 MiB** peak CLI
process RSS. This timer includes subprocess startup through artifact writing;
fixture creation is excluded. Final scoring-only runs took **0.152 s** for a
10,000-module chain, **0.127 s** for a 10,000-module cycle and **0.784 s** for
40,000 modules in disconnected pairs. These measurements are observational,
not release thresholds.

Python-specific syntax tests are version-gated. The
[CI workflow](../.github/workflows/tests.yml) is configured for Python 3.11,
3.12, 3.13 and 3.14 using their latest patch releases; the package requirement
rejects CPython 3.14.1. Local results on additional interpreters are recorded
below; the hosted CI workflow has not been run from this workspace.

| Local interpreter | Full suite | Corpus |
| --- | --- | --- |
| CPython 3.12.12 | 363 passed, 1 skipped (PEP 696 default syntax) | 28 successful runs |
| CPython 3.13.13 | 364 passed | 28 successful runs |
| CPython 3.14.2 | 364 passed | 28 successful runs |

The 3.12 and 3.13 runs reused the locked pure-Python dependencies from the local
3.14 environment through `PYTHONPATH`; they were not independent installation
tests. Python 3.11 was unavailable locally and awaits the configured CI job.
