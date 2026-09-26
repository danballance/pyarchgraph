# Dependency scenarios

The 25 projects under `projects/` are committed source fixtures covering clean
architecture, cycles, boundary violations, and uncertain imports. The test
harness reads their source; it never imports or executes the applications.

[manifest.json](manifest.json) defines all 28 runs, including source roots,
exclusions, forbidden dependencies, expected exit codes, module/dependency
counts, and findings. Every variant has a complete configuration. The three
variants exercise explicit test exclusion, an incorrect source root, and a
documentation edit that moves import locations.

Run the acceptance tests and print the complete expected/actual matrix:

```sh
.venv/bin/python -m pytest tests/test_examples.py -q
.venv/bin/python -m examples.evaluate
```

The evaluator runs the actual CLI for every scenario. It exits successfully
only when every result matches the manifest: a fixture designed to fail is a
successful acceptance check when it produces the expected failure. Unexpected
findings, missing findings, changed certainty/counts, invalid output, and wrong
exit codes fail validation. Add `--json` for machine-readable results or repeat
`--project NAME` to select projects:

```sh
.venv/bin/python -m examples.evaluate --project spelling_relative --project spelling_absolute --json
```

A single scenario can also be checked directly:

```sh
.venv/bin/python -m pyarchgraph examples/projects/src_root_hazard/src
.venv/bin/python -m pyarchgraph examples/projects/dense_ordered_dag --forbid presentation:repository
```

Reports go to stdout. Exit `0` means no blocking findings; exit `1` means
findings require attention. Exit `2` is an analysis/configuration error with an
explanation on stderr and no partial JSON report.

## Expected scenarios

| Project | Expected result |
| --- | --- |
| `layered_service` | Pass: four legitimate layers, with no forbidden presentation-to-repository shortcut. |
| `ports_and_adapters` | Pass: the workflow consumes a protocol; composition selects infrastructure. |
| `definite_cycle` | Fail: two exact imports form a definite cycle. |
| `self_import` | Fail: one module imports itself, with a one-edge witness. |
| `cycle_with_independent_pair` | Fail: an unrelated feature cannot conceal the original cycle. |
| `cycle_with_49_pairs` | Fail: 49 unrelated dependency pairs cannot conceal the original cycle. |
| `cycle_with_test_padding` | Fail in both runs: tests are always excluded, leaving two modules and two dependencies. |
| `cycle_with_isolated_modules` | Fail: isolated modules cannot conceal the original cycle. |
| `dense_ordered_dag` | Fail: an acyclic graph violates the configured presentation-to-repository rule. |
| `spelling_relative` | Pass: relative child imports have the same architecture as the absolute spelling. |
| `spelling_absolute` | Pass: absolute spelling produces the same dependency count. |
| `shadowed_package_attribute` | Fail: a possible cycle remains visible without claiming it is definite. |
| `type_only_cycle` | Fail: typing-only imports remain structural dependencies. |
| `local_import_cycle` | Fail: function-local imports remain structural dependencies. |
| `mixed_import_evidence` | Fail: duplicate ordinary and typing-only imports retain evidence without double-counting an edge. |
| `src_root_hazard` | Fail from `src`; analysis error from the incorrect repository root. |
| `missing_internal_target` | Fail: the missing internal module has an explanatory finding. |
| `valid_namespace_package` | Pass: a valid namespace base is distinct from a missing internal target. |
| `dynamic_literal` | Fail: both dynamic calls require review and contribute a possible cycle. |
| `dynamic_aliases` | Fail: common aliases cannot hide any of the three dynamic calls. |
| `dynamic_nonliteral` | Fail: both runtime-selected targets require review. |
| `scc_not_simple_cycle` | Fail: one three-module component gets one bounded witness. |
| `dense_cyclic_component` | Fail: many simple cycles get one bounded component witness. |
| `legitimate_package_reexport` | Pass: public package APIs retain dependencies through initializers. |
| `location_only_edit` | Fail in both runs: documentation moves evidence lines without changing semantic findings. |

The complete corpus expects **6 passes, 21 finding failures, and 1 analysis
error**. Tests additionally check source locations, bounded cycle witnesses,
normalization, preserved import evidence, and equivalent graphs.

## Historical evidence

[review-baseline.json](review-baseline.json) is the immutable snapshot from
commit `925276dbedbc324427a619aab91a35a719e8e183`. Its old scores and policy
measurements are archival evidence, not acceptance requirements. The acceptance
tests verify that the committed fixture source hashes still match that snapshot.

The simplified tool intentionally changes these expectations:

- Numerical scores, cleanup debt, baseline comparisons, and evidence filters
  are absent from the report and acceptance criteria.
- Tests are always excluded, so both test-padding runs analyze exactly two
  modules and two dependencies.
- Possible cycles and all recognized dynamic calls block a pass.
- A wrong source root is an analysis error, with no partial report.
- Harmless probable child relationships and valid namespace bases can pass.

## Extending the corpus

Add readable source and a local explanation under `projects/<name>/`, then
register a complete default run and any variants in the manifest. Define the
expected behavior from the scenario before running the implementation. Keep
structural assertions when counts alone would miss the intended relationship.
Do not rewrite expectations to accommodate a regression, or modify sample
applications simply to make acceptance checks pass.

Preserve the historical snapshot. Any deliberate fixture update also needs a
reviewed update to the source-integrity check; do not replace old observations
with measurements from the new implementation.
