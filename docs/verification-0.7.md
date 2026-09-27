# 0.7 verification

The pre-change baseline was 428 tests and 40 corpus runs. The migration preserves
all original test function names. The original fixture applications and archival
validation artifacts have no changes. The frozen extraction reference has SHA-256
`420a57a745bf466cb856fb22a14f651167d5dab6a12d726b6527afe61a55f642`, matching its
recorded hash in the archived context benchmark.

The supported CI matrix remains Python 3.11, 3.12, 3.13, and 3.14, excluding
3.14.1. PEP 695 tests require 3.12 and PEP 696 tests require 3.13; skips on older
interpreters are expected. Matrix tests and corpus evaluation use the existing
local environments with pytest 8.4.2 and NetworkX 3.6.1.

## Tests and corpus

Final verification collected 569 tests on every supported Python minor version:

| Python | Passed | Expected syntax skips | Separate corpus evaluation |
| --- | ---: | ---: | --- |
| 3.11.14 | 526 | 43 | 40/40 matched |
| 3.12.12 | 548 | 21 | 40/40 matched |
| 3.13.13 | 569 | 0 | 40/40 matched |
| 3.14.2 | 569 | 0 | 40/40 matched |

Every command exited 0. The full tests also exercise the real CLI corpus;
the separate evaluator was additionally run once per interpreter. The new tests
cover import boundaries, injected core services, service reuse after failures,
registry selection and removal, projected cycles and self-loops, fabricated
provenance, immutable output validation, advisory severities, mandatory coverage,
CLI extension failures, and the external package-view/check example.

```console
python -m pytest -q
python -m examples.evaluate --json
```

Local matrix logs are `/tmp/pyarchgraph-07-final-tests-{311,312,313,314}.log` and
`/tmp/pyarchgraph-07-final-corpus-{311,312,313,314}.json`. Test durations are not
performance measurements: the four matrix jobs ran concurrently.

## Packaging

Offline wheel and sdist builds were inspected. Both contain runtime version
0.7.0, all 29 runtime Python files, and none of the removed flat runtime modules.
An isolated installation passed the public API smoke test. The installed console
entry point and `python -m pyarchgraph` produced identical output. Local artifacts
were written under `/tmp/pyarchgraph-package-review-xh7_f4j5`. The working
environment was also refreshed offline; installed metadata reports 0.7.0 and
the console entry point targets `pyarchgraph.__main__:main`.

## Reproducible performance comparison

The paired pipeline benchmark extracts baseline runtime commit
`caac3f23bd879cdf51fe78dae76edca06ec82d8a` into a temporary directory. Use
`--baseline-ref` to select another 0.6 baseline. It compares normalized reports,
exit codes, and stderr for every corpus run and a synthetic graph containing
240 modules arranged in 30 dense components. The optional archived SymPy case
adds a larger real project without executing its source code.

```console
python -m benchmarks.pipeline_migration --repeats 3 --research-case sympy --output /tmp/pyarchgraph-07-pipeline.json
python -m benchmarks.collection --repeats 5 --output /tmp/pyarchgraph-07-collection.json
python -m benchmarks.context_optimization --repeats 3 --output /tmp/pyarchgraph-07-context.json
python -m benchmarks.replay_validation --output /tmp/pyarchgraph-07-replay
```

Pipeline timings include CLI parsing, discovery, extraction, resolution, graph
analysis, and JSON rendering. They exclude process startup and schema
normalization. Alternating baseline/current order reduces order bias. Results
from one machine describe these workloads and do not establish general speedups.
The archived SymPy checkout is required for the research and context runs; it is
not downloaded or installed by these scripts.

## Final pipeline results

All 42 cases produced identical schema-normalized reports, exit codes, and stderr
between the pinned 0.6 baseline and 0.7. Normalization removes only the documented
schema additions/envelopes and renames view/count keys. The runtime source hashes
remained unchanged during measurement. Python 3.14.2 was used for both versions.

The measured regression is **17.2% in median CPU time and 18.5% in median wall
time** on the combined workload. The final paired samples are:

| Pair | Baseline CPU (s) | 0.7 CPU (s) | Baseline wall (s) | 0.7 wall (s) |
| --- | ---: | ---: | ---: | ---: |
| 1 | 17.311 | 20.283 | 17.642 | 20.902 |
| 2 | 17.591 | 20.638 | 17.874 | 22.185 |
| 3 | 17.000 | 18.510 | 17.289 | 18.864 |

| Workload | Baseline median CPU (s) | 0.7 median CPU (s) | Change |
| --- | ---: | ---: | ---: |
| Archived SymPy scope | 16.639 | 19.275 | +15.8% |
| 240-module dense components | 0.378 | 0.424 | +12.1% |

Samples vary, and this result does not attribute all overhead to any one layer.
Investigation identified additional graph validation/canonicalization work and
some rendering overhead. A frozen index shared within each analysis, reuse of
canonical immutable records, and a scalar serialization fast path were applied
without skipping validation; the figures above measure the final implementation.
The earlier investigative profiles also showed variation in unchanged AST
compile/traversal operations, so their cumulative differences are not causal
estimates of the migration's cost. The larger report and extension validation
have not been shown to be performance-neutral.

All paired samples, per-case timings, equality hashes, and current runtime source
hashes are preserved in `/tmp/pyarchgraph-07-pipeline.json`. The preceding run is
retained separately at `/tmp/pyarchgraph-07-pipeline-before-optimization.json`;
it is not substituted for these final results.

## Extraction reference results

The synthetic collection benchmark compared all 8,000 facts and canonical IDs
for 1,000 statements with eight aliases each over five paired runs. Every result
was identical to the retained `ast.get_source_segment` reference. Median times
were 24.109 s for the reference and 0.307 s for cached extraction (78.46× faster).
This compares the retained pre-optimization extraction implementation, not the
0.6 whole pipeline; it does not offset or contradict the regression above.

| Run | Reference (s) | Cached (s) |
| --- | ---: | ---: |
| 1 | 20.474 | 0.280 |
| 2 | 24.109 | 0.293 |
| 3 | 25.520 | 0.368 |
| 4 | 27.264 | 0.397 |
| 5 | 24.000 | 0.307 |

The full equality and timing record is `/tmp/pyarchgraph-07-collection.json`.

The real SymPy collection comparison covered 864 source modules and 19,920 import
facts over three paired runs. Facts, canonical IDs, context annotations, and
diagnostics were identical to the frozen reference. Median current extraction
was 14.890 s versus 24.992 s for the reference (1.678× faster).

| Run | Frozen reference (s) | Current extraction (s) |
| --- | ---: | ---: |
| 1 | 23.800 | 18.698 |
| 2 | 25.487 | 14.890 |
| 3 | 24.992 | 14.455 |

The full record, including source hashes and exact collection digest, is
`/tmp/pyarchgraph-07-context.json`. Both extraction benchmarks check complete
immutable results after every run and alternate execution order.

## Archived campaign replay

The full live replay completed successfully for all 33 configurations: 29 original
research scopes and four supplementary boundary/multi-root scopes. All 23
expected-delta checks passed. Independent checks audited 1,574 displayed evidence
entries and found no missing saved restricted exact edges, component mismatches,
invalid witnesses, or report/exit inconsistencies. Every run used the same
runtime source hashes.

All 361 archival files retained their original SHA-256 hashes before and after
the replay. Target projects were parsed, never imported, installed, or executed.
The existing archived working directories were reused; their recorded upstream
commit identities and source bytes were not independently reverified. Restricted
edge comparisons cover the saved oracle subset, not overall precision/recall.

Detailed reports, source/evidence audits, profiles, expected-delta results, and
archive hashes are under `/tmp/pyarchgraph-07-replay/`. The summary is
`replay-summary.json`, with `all_passed`, `expected_deltas_passed`, and
`archival_files_unchanged` all true.
