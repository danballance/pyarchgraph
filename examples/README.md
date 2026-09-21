# Architecture review corpus

These 25 small projects are committed Python source, designed to make the
review's claims reproducible. Each directory under `projects/` is an independent
project. Most contain a readable application scenario; the large pair and test
padding projects are explicit mathematical controls. No example application is
imported or executed by the test harness.

The [manifest](manifest.json) declares each project's source root, exclusions,
expected package names, graph policy, category, intent, historical measurements,
and desired structural outcomes. Three projects also declare alternative runs:
production-only test exclusion, an incorrect repository root, and a source edit
that changes import locations without changing dependencies.

Run the acceptance corpus from the repository root:

```sh
.venv/bin/python -m pytest tests/test_examples.py -q
```

Print the reviewed and current scores beside definite/possible finding counts
and the policy result. Add `--json` for metrics and current provenance, or repeat
`--project NAME` to select specific projects:

```sh
.venv/bin/python -m examples.evaluate
.venv/bin/python -m examples.evaluate --project spelling_relative --project spelling_absolute --json
```

This report reads the manifest at run time, so it does not store a stale snapshot
of the evolving implementation. The intentionally bad examples report failures;
the report command itself succeeds so it can be used to inspect the whole corpus.

For an individual project, the command-line interface accepts its explicit
import root. For example:

```sh
.venv/bin/python -m pyarchgraph examples/projects/src_root_hazard/src \
  --project-root examples/projects/src_root_hazard --expect-package pkg \
  --json-only --output-dir /tmp/pyarchgraph-example
```

The production policy excludes tests by default. The main
`cycle_with_test_padding` manifest run deliberately sets `include_tests: true`;
its `production_only` variant excludes `tests/**`. The manifest records these
choices so that the two scores can be interpreted correctly.

## Projects and the question each answers

| Project | Category | Intended outcome |
| --- | --- | --- |
| `layered_service` | Good | Four legitimate layers are acyclic; an 85 score does not justify a failure. |
| `ports_and_adapters` | Good | Composition selects infrastructure while the workflow consumes a protocol. |
| `definite_cycle` | Failure | Two exact imports justify a definite cycle finding. |
| `self_import` | Failure | A self-import is a one-module cyclic component with a one-edge witness. |
| `cycle_with_independent_pair` | Control | An unrelated feature raises the score without resolving the cycle. |
| `cycle_with_49_pairs` | Control | A score above 98 still accompanies the original definite cycle. |
| `cycle_with_test_padding` | Control | Test consumers dilute the score; explicit production scope preserves the cycle. |
| `cycle_with_isolated_modules` | Control | Isolated inventory cannot dilute the active-module score. |
| `dense_ordered_dag` | Warning | More direct imports need a directional rule even when reachability is unchanged. |
| `spelling_relative` | Warning | Relative child-module imports have the same architecture as their absolute equivalent. |
| `spelling_absolute` | Warning | The equivalent absolute spelling does not receive an architectural advantage. |
| `shadowed_package_attribute` | Warning | A shadowed child candidate cannot support a definite cycle failure. |
| `type_only_cycle` | Warning | Typing dependencies remain structural; an explicit filtered policy can omit them. |
| `local_import_cycle` | Warning | Function-local imports remain structural; eager runtime behavior is a separate concern. |
| `mixed_import_evidence` | Failure | Removing typing evidence retains an edge with ordinary evidence. |
| `src_root_hazard` | Failure | The real cycle is visible from `src`; a wrong root invalidates scope and suppresses the score. |
| `missing_internal_target` | Failure | Complete parsing does not make a missing internal dependency complete. |
| `valid_namespace_package` | Warning | An unmodelled namespace base is different from an absent source module. |
| `dynamic_literal` | Warning | Literal dynamic targets retain uncertainty and explicit call evidence. |
| `dynamic_aliases` | Warning | Common module and function aliases do not hide dynamic calls. |
| `dynamic_nonliteral` | Warning | Runtime-selected target names remain visible limitations. |
| `scc_not_simple_cycle` | Failure | A three-module SCC can contain only two-module simple cycles. |
| `dense_cyclic_component` | Failure | Many simple cycles require one bounded component witness. |
| `legitimate_package_reexport` | Good | Public package APIs retain real dependencies through their initializers. |
| `location_only_edit` | Control | Comments and blank lines change evidence locations, not violation identity. |

The categories describe the lesson a project illustrates. A warning example
may contain a cycle under the full structural policy, and a good example need
not score above an arbitrary numerical threshold. The `desired` objects make
the intended outcomes explicit instead of inferring them from those labels.

## Before-change evidence

[review-baseline.json](review-baseline.json) was captured using an untouched
copy of commit `925276dbedbc324427a619aab91a35a719e8e183`, before changing the
implementation. It records the analyser version, commit and Python-file hashes,
interpreter version, formula and graph policy, per-run roots and exclusions,
module/edge/cycle/reachability metrics, unresolved imports, dynamic warnings,
and SHA-256 hashes of every fixture source file. All 28 baseline runs parsed
successfully.

The baseline is historical evidence, not a specification to preserve its bugs.
In particular:

| Historical observation | Reviewed result | Desired current behavior |
| --- | --- | --- |
| Relative versus absolute spelling | 33.3333 versus 85; two versus zero cyclic modules | Same architecture and score; original syntax evidence retained. |
| Shadowed package attribute | A candidate relationship contributes to a cycle | Possible cycle is separate from a definite blocking finding. |
| Incorrect source root | 100 with `complete=true` | Invalid scope, no score, and an explicit root/package warning. |
| Aliased dynamic calls | Zero warnings | Recognized aliases and literal target evidence; nonliteral calls remain unresolved warnings. |
| Comment/whitespace-only edit | Import fact identities change | Semantic dependency and finding identities stay stable. |

The scoring controls intentionally keep the existing formula's limitations
visible. These are arithmetic consequences, rather than resolution bugs:

| Unchanged graph formula | Score |
| --- | ---: |
| Two-module cycle | 0 |
| Same cycle plus one unrelated directed pair | 57.5 |
| Same cycle plus 49 unrelated directed pairs | 98.4455 |
| Same cycle plus 100 test consumers | 98.0392 |
| Four-module chain | 85 |
| Fully ordered four-module DAG | 85 |

The tests separately assert the stable formula controls and the corrected
behavior. A high score never clears a definite cycle; a directional rule can
reject a new direct edge even when the score does not change. These checks
require no numerical threshold and contain no expected-failure markers.

## Extending the corpus

Add readable source under a new `projects/<name>/` directory, explain its intent
in a local README, and register the source root and desired result in the
manifest. Keep production and test scope explicit. Use named variants when the
source or scope is deliberately comparable. The harness passes those declared
settings to the public analysis API and checks source locations on cycle
witnesses without importing the fixture code.

The committed historical snapshot is tied to these exact sources. If fixtures
change, preserve the original evidence in version history and deliberately
capture a new baseline with recorded analyser identity; do not rewrite old
observations using the fixed implementation. A formula version alone cannot
identify changes to resolution or evidence filtering.
