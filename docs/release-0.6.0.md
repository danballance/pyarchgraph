# 0.6.0 release candidate

This release replaces JSON schema 0.5 with 0.6 and updates the Python API. Upgrade
PyArchGraph and Checksmith together; neither adapter includes a legacy parser.
The build artifacts are local release candidates. Publication is a separate step.

## Consumer migration

- Replace top-level counts/findings with `sources`, `coverage`, and the three
  named `views`. Read `gate` to choose the blocking view.
- Interpret source IDs through the `sources` inventory, rather than assuming
  graph endpoints are dotted import names.
- Parse exit-2 JSON: incomplete results retain recovered findings and coverage
  diagnostics. CLI/configuration errors can still have stderr and no report.
- Keep incomplete coverage dominant over both clean and failing partial graphs.
- Treat nonselected views as informational and resolution certainty independently
  from typing/function context.
- Use `analyse((Path("src"),), options=AnalysisOptions(...))` in Python callers.
- The example manifest is schema 3 and supplies explicit roots, gate, detail and
  optional configuration per run.

Checksmith's paired change uses strict schema 0.6 models, preserves partial
observations under its error result, shows the selected and informational view
summaries, and pins the default package to `pyarchgraph==0.6.0`. That pin becomes
usable through the configured package index when 0.6.0 is published. Test local
integration against the checkout or built wheel until then.

## Behaviour changes

Ordinary-module `from` self-imports now retain self-edges. Discovery analyzes
unusual Python filenames and path-only sources, records exclusions, and supports
multiple explicit roots. Ambiguous cross-root bindings and corroborated root
mismatches prevent a complete result.

Shared namespace ownership is inferred at owned source/package branches, with
explicit overrides for namespace-only projects. Stub, native and declared
generated targets are visible implementation boundaries requiring acknowledgement.
Acknowledgement never excuses a parse failure or ambiguous binding.

All reports include structural, non-typing and module-body views. Structural is
the default gate. Evidence is deduplicated and annotated with import context;
complete component edges are available with `--details component-edges`.

Collection now caches physical source lines and extracts each statement snippet
once. Context collection skips expression subtrees, which cannot contain import
statements, and avoids alias analysis when no module-level typing alias exists.
The conservative rebinding scan still checks expression-level assignments.
The reproducible benchmarks in `benchmarks/collection.py` and
`benchmarks/context_optimization.py` compare exact collection results against
retained reference implementations. Their timings measure collection, rather
than whole-application performance.

## Validation

Run the full test suite and the 40-run example corpus. Python 3.11–3.14 are
supported, excluding 3.14.1. Syntax-version-specific tests skip only when the
interpreter cannot parse that syntax.

`benchmarks/replay_validation.py` replays the pinned research snapshots without
executing target applications. Fresh reports, independent evidence/graph checks,
source hashes and acceptance results go under `docs/validation/0.6`. The original
`docs/validation/2026-09-26` records remain unchanged.

Before coordinated publication, rebuild the wheel/sdist from the final reviewed
sources, run the complete Checksmith integration suite, and publish 0.6.0 together
with its matching adapter. No remote tag, push or package publication is performed
by the local implementation work.
