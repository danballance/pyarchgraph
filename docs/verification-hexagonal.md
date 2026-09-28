# Hexagonal layout refactor verification

Verified on 2026-09-28 against pre-refactor commit
`cb787e58c79c1e860a557ba605f9e51384861fdb`. The baseline had 569 passing tests on
Python 3.14.2. The refactor changes Python imports and the request interface;
package version 0.7.0, JSON schema 0.7 and CLI behaviour remain unchanged.
See [the architecture and API migration](architecture.md) for the new interfaces.

## Tests and boundaries

The final suite collects 629 cases. Existing non-architecture test functions were
retained and regrouped by execution scope; additional cases cover draft/final
identity, request validation before I/O, multi-root canonicalization, CLI status
policy and recursive architecture enforcement.

| Python | Passed | Expected syntax skips |
| --- | ---: | ---: |
| 3.11.14 | 586 | 43 |
| 3.12.12 | 608 | 21 |
| 3.13.13 | 629 | 0 |
| 3.14.2 | 629 | 0 |

Every matrix command exited 0. The older interpreters skip PEP 695/696 syntax
they cannot parse. Matrix runs include the real CLI corpus. The separate current
corpus evaluator also matched all 40 configured runs across 31 example projects.

All four import-linter contracts pass. The 90 architecture cases include actual
linter runs against invalid nested fixtures, type-checking imports, permitted
same-component collaboration, core purity checks and cold imports with NetworkX
blocked. All core class/method annotations also resolved during review.

The application analyzed its own runtime with examples, docs and benchmarks
excluded: 40 source files, complete coverage, zero cyclic nodes and zero findings
in all three views. Ruff import/undefined-name checks and formatting checks pass
for the changed runtime, tests and active tooling. `git diff --check` passes.

```console
uv run lint-imports --no-cache
uv run python -m pytest -q
uv run python -m examples.evaluate --json
uv run pyarchgraph . --exclude examples --exclude docs --exclude benchmarks
```

Local matrix logs are `/tmp/pyarchgraph-hex-tests{311,312,313,314}.log`.
The corpus and self-analysis reports are `/tmp/pyarchgraph-hex-corpus.json` and
`/tmp/pyarchgraph-hex-self.json`.

## Behaviour and benchmark comparisons

The 40 corpus runs have byte-identical stdout, stderr and exit codes compared
with the saved baseline. A reproducible paired pipeline comparison additionally
checks a 240-module synthetic graph: all 41 cases match exactly over three
alternating repetitions, without schema normalization. Separate exact comparisons
also passed for CLI help, an invalid gate and a missing configuration file.

```console
uv run python -m benchmarks.pipeline_migration --baseline-ref cb787e58c79c1e860a557ba605f9e51384861fdb --exact-schema --repeats 3 --output /tmp/pyarchgraph-hex-pipeline.json
uv run python -m benchmarks.collection --repeats 2 --statements 200 --aliases 8 --output /tmp/pyarchgraph-hex-collection.json
uv run python -m benchmarks.context_optimization --repeats 1 --output /tmp/pyarchgraph-hex-context.json
uv run python -m benchmarks.discovery --sizes 1000 2000 --repeats 2
```

The paired pipeline's median CPU time was 0.363 seconds before and 0.322 seconds
after; median wall time was 0.365 and 0.325 seconds. These short local measurements
show no regression on this workload and are not a general performance claim.

The synthetic collector comparison matched all 1,600 final facts and IDs. The
context collector comparison matched 19,920 facts, contexts, IDs and diagnostics
across 864 sources in the existing archived SymPy checkout. Both collector timings
include canonicalization; comparison/serialization happens outside the timers.
The collection digest remains
`385b7e0368cd54ef1c26630c944a1b9dea76b246abcc6e7536daaa1bc7391857`.

The reference collector retains its old algorithm and uses benchmark-local legacy
fact values. Its imports and protocol inheritance were adapted to separate that
old lifecycle from the runtime's new drafts. AST comparison confirmed its 15
definitions otherwise remain unchanged. Current SHA-256 values are:

- `benchmarks/reference_extraction.py`:
  `d6f3847cc28ae524c41b3ebb53856cb7740257aa469b23aef9df6e7ea5aa393a`
- `benchmarks/reference_models.py`:
  `eb8d76d018602642fb94d35a8c7fb4cc7846fc495d26bd301f86d65aac9d4647`

Historical research, release records and example fixture sources were not changed.
The full archived research campaign was not rerun; the targeted collector and
pipeline checks above supplement the test/corpus coverage.

## Distribution checks

Wheel and source-distribution builds succeeded. The wheel was built from the
source distribution, and archive inspection checks the nested runtime packages,
documentation and removal of old module paths. A separate environment installed
the wheel with the locked NetworkX dependency. From outside the checkout, the new
Python request API correctly detected a two-module cycle; the installed console
entry point and `python -m pyarchgraph` produced identical JSON, stderr and status.

```console
uv build --out-dir /tmp/pyarchgraph-hex-dist
```

Artifacts and the isolated installation are under `/tmp/pyarchgraph-hex-dist`
and `/tmp/pyarchgraph-hex-installed`. Checksmith migration and publishing were not
performed.
