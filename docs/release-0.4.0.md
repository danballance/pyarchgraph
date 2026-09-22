# Version 0.4.0: cleanup debt and comparisons

Version 0.4.0 adds `policy-debt-v1` to canonical JSON schema `0.4`, providing a
lower-is-better count of observed policy violations and actionable cleanup work
items. This supports agent-guided architecture cleanup through CLI JSON.
The experimental `architecture-v1` score remains advisory and unchanged.

## Cleanup report

`cleanup.violation_count` sums definite `cyclic_dependency` and
`forbidden_dependency` counts. Each directed dependency in a definite cycle
counts once, including self-imports. A dependency forbidden by several rules
counts once in that category; an edge violating both policies counts twice.
Repeated imports and clean additions cannot dilute debt. The total measures
observed violations, not repair effort or general code quality.

`violations` retain certainty, stable kind/source/target identities and import
evidence. `possible_violation_count` keeps possible violations outside the
definite count. Certainty is calculated per dependency, including components
containing both definite and possible cycles. `work_items` group definite cycle
violations by component and forbidden dependencies individually, ordered by
decreasing definite count and stable identity. Cycle witnesses are bounded;
removing one witness edge need not repair the whole component.

Coverage limitations remain visible alongside observed counts. `cleanup_complete`
is true only when the policy check returns `pass`. Zero definite violations with
missing targets, dynamic-import uncertainty, incomplete source analysis or invalid
scope does not establish completion. Ordinary supported type aliases do not
create debt; dynamic imports within aliases retain possible evidence. Generic
function/class bounds and defaults remain unsupported and prevent completion.

## Compare edits without hiding regressions

`--cleanup-baseline PATH` writes a comparison inside `cleanup.comparison` and
requires JSON selection. It is mutually exclusive with `--baseline`, whose
existing comparison behavior is preserved. Both options support
`--allow-inventory-change` after inventory review.

Comparisons validate and derive evidence on each side, rather than trusting
saved counts. They distinguish newly observed, newly confirmed, persistent,
lost-certainty, verified-resolved, disappeared-unverified and removed-with-module
violations. Verified resolution requires sufficient current coverage and no
removed module identities. Deleting an intermediate module can hide a cycle
between retained endpoints, and a dangling flat import may appear external;
such disappearances remain unverified. Removed endpoints are classified
separately, and renames are not inferred.

Inspect `has_new_violations` and `needs_review` independently of `count_delta`
(current definite count minus baseline count). A lower total can coexist with a
new violation. An exact import replaced by uncertain dynamic evidence is a loss
of certainty. Structurally valid incomplete reports can retain useful comparison
evidence without claiming unsupported repairs.

The [README workflow](../README.md#cleanup-comparisons) captures a baseline,
inspects work items, makes a behavior-preserving edit, runs project tests and
compares fresh reports. Keep the original baseline for cumulative progress and
optionally compare each iteration separately.

## Compatibility and output

Generate new baselines with this release. Compatibility requires matching
analyzer identity and source digest, interpreter, source root, exclusions,
evidence policy, rules and model versions. Model names alone do not establish
equivalent extraction semantics, and historical baselines are not migrated.
The debt model version is included in provenance.

JSON-producing runs include cleanup and print its summary on stderr.
`--output score` continues to emit only the existing architecture-score summary
on stdout, preserving its experimental label. Score-only runs still create no
artifacts, graph-only and score-only runs cannot request cleanup comparison,
and unselected destinations remain untouched. Output uses existing staged
publication.

The `quality` object, formula and `--check` exit codes are unchanged. Policy
checks still evaluate all current violations; baselines do not exempt existing
debt. This release introduces no scalar quality gate, budget, inferred rules,
automatic refactoring or dedicated Python comparison API.

## Verification

Regression coverage exercises dilution controls, forbidden shortcuts, overlapping
rules, self-imports, mixed-certainty cycles, SCC changes and deterministic work
items. Comparison tests cover lower totals with new violations, location-only
edits, certainty changes, incomplete analysis, module inventory changes and
malformed or incompatible baselines. CLI tests preserve output selection,
streams, publication and existing policy checks. Supported type-alias syntax and
dynamic alias evidence are covered according to interpreter support.

The corpus evaluator displays current definite and possible debt beside the
historical advisory scores; it does not modify historical evidence. All 28
configurations completed and round-tripped through cleanup comparison, including
uncertain and invalid-scope reports. Cleanup completion agreed with the policy
check in every configuration.

The full test matrix passed on Linux, using locked dependencies and separate
interpreter environments:

| Python | Passed | Skipped |
| --- | ---: | ---: |
| 3.11.14 | 531 | 72 |
| 3.12.12 | 597 | 6 |
| 3.13.13 | 603 | 0 |
| 3.14.2 | 603 | 0 |

Skips cover syntax unavailable to the older interpreters. Ruff checks and
`git diff --check` passed; offline source-distribution and wheel builds succeeded.

The extended full-CLI benchmark verified a 2,000-module cycle with exactly 2,000
violations and one work item, and a 2,000-module layered graph with 9,323 imports
and zero debt. Single concurrent runs on the development machine took 2.21 s
and 3.28 s respectively, with peak process RSS of 72.1 MiB and 104.7 MiB.
These are observations, not performance thresholds. Tests independently check
100 generated graphs against return-path reachability and forbid simple-cycle
enumeration in the dense-component cleanup path.
