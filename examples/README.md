# Architecture acceptance corpus

Run `uv run python -m examples.evaluate` from the repository root. The evaluator
checks 40 real CLI invocations over 31 small projects against independently
specified expectations in `manifest.json` (manifest schema 3). It never executes
fixture application code.

The original 25 projects preserve their source bytes and archival hashes in
`review-baseline.json`. Their original structural expectations remain, with the
wrong-root scenario now returning an incomplete JSON report. Four extra view
variants exercise the existing typing-only and deferred-import cycles.

Six new projects cover multiple roots, unusual source filenames, partial analysis,
shared namespace ownership, acknowledged native implementation boundaries and
ordinary-module `from` self-imports. Native boundaries include both an accepted
variant and a filtered-gate variant that remains incomplete.

The 40 expected outcomes are 15 passes, 21 findings reports and four incomplete
reports. These are mechanism tests, not project-quality measurements.

Manifest findings use readable import names; the evaluator resolves the report's
opaque source IDs through its source inventory before comparing those
expectations. Schema, coverage status, view shape, exit code, counts and findings
are checked separately. Source evidence and cycle witness closure receive
additional pytest checks.
