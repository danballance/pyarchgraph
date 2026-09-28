# Architecture acceptance corpus

Run `uv run python -m examples.evaluate` from the repository root. The evaluator
checks 40 real CLI invocations over 31 small projects against independently
specified expectations in `manifest.json` (manifest schema 3). It never executes
fixture application code.

The original 25 projects retain their archival hashes and observations in
`review-baseline.json`. On 2026-09-28, the ports-and-adapters email adapter was
updated to inherit its delivery protocol explicitly, adding a seventh dependency.
Its current source hash is recorded as an explicit override in the corpus
integrity test; all other original source hashes remain enforced. The wrong-root
scenario now returns an incomplete JSON report. Four extra view variants exercise
the existing typing-only and deferred-import cycles.

Six new projects cover multiple roots, unusual source filenames, partial analysis,
shared namespace ownership, acknowledged native implementation boundaries and
ordinary-module `from` self-imports. Native boundaries include both an accepted
variant and a filtered-gate variant that remains incomplete.

The 40 expected outcomes are 15 passes, 21 findings reports and four incomplete
reports. These are mechanism tests, not project-quality measurements.

Manifest findings use readable import names; the evaluator resolves the report's
opaque source IDs through its source inventory before comparing those
expectations. Schema 0.8 finding envelopes are unwrapped for these independently
maintained semantic expectations. Exact view IDs retain their hyphens.
Schema, coverage status, view shape, exit code, counts and findings
are checked separately. Source evidence and cycle witness closure receive
additional pytest checks.

Every default run now also reports `package-structural`, `package-non-typing` and
`package-module-body`. The original source-gate expectations still describe the
same source dependencies. Explore package grouping on an existing fixture with:

```console
uv run pyarchgraph examples/projects/valid_namespace_package --gate package-structural
uv run pyarchgraph examples/projects/valid_namespace_package --gate package-non-typing --package-max-depth 1
```

Package reports include all dependencies and source evidence, regardless of
whether a cycle exists. Namespace source modules group without initializers;
imports of the namespace container alone do not add graph edges. The independent
`custom_strategies.py` example demonstrates extension contracts and its own
coarser grouping rule; it does not define the built-in package-view semantics.
