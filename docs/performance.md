# Import extraction and CLI performance

The reviewed import-ID helper compared every digest with every other digest,
and the default analysis pipeline assigned IDs twice. The helper now sorts
distinct SHA-256 digests and compares adjacent pairs. Each digest's longest
shared prefix occurs with one of its neighbours, so the replacement preserves
the existing IDs and extends collisions identically in O(n log n) time for
fixed-length hashes. Full digest collisions still raise an error.

The public `AstImportFactSource.collect()` continues to return canonical IDs.
The default `analyse()` path collects raw facts and canonicalises once at the
pipeline boundary, where injected fact sources also receive canonical IDs.

## Measurements during implementation

Measured on 2026-09-21 with CPython 3.14.2 on Linux x86-64. These wall-clock
samples came from a shared development machine; results vary with machine load.
The full measurements and commands are in [benchmark-review.json](benchmark-review.json).

| Import digests | Reviewed prefix helper | Sorted-neighbour helper |
| ---: | ---: | ---: |
| 1,000 | 0.565376 s | 0.004050 s |
| 2,000 | 2.148216 s | 0.006987 s |
| 4,000 | 10.918375 s | 0.029298 s |
| 8,000 | 37.706427 s | 0.042382 s |

The complete updated CLI was measured separately against a generated layered
project containing 2,000 modules and 9,323 imports/dependencies. All runs produced
the complete inventory and the expected score of 85.0.

| Updated CLI mode | Three elapsed times | Median | Peak child-process RSS |
| --- | --- | ---: | ---: |
| JSON only | 4.828970, 5.212485, 5.572186 s | 5.212485 s | 116.8 MiB |
| JSON and Mermaid | 14.066671, 10.568753, 11.026268 s | 11.026268 s | 162.6 MiB |

These CLI times include process startup, discovery, extraction, ID assignment,
resolution, graph analysis, JSON serialization and writing artifacts. Mermaid
mode additionally includes diagram rendering and its transitive reduction.
Fixture generation and subsequent inspection of the artifacts are outside the
timer. Both modes use the updated implementation: no full-CLI speedup over the
reviewed version is inferred from the isolated prefix timings.

## Final-code verification

After all product fixes, the JSON-only CLI was rerun against the same 2,000
modules and 9,323 imports. The three runs took 6.032866, 4.647948 and 4.487964
seconds: **4.647948 seconds median**, with 117.0 MiB peak child-process RSS.
The result retained all modules/imports, scored 85.0 and wrote no diagram.
`final_json_only_validation` in the measurement JSON records the analyser
version, base commit and final source digest, including these uncommitted changes.
The earlier two-mode comparison remains labelled as implementation-time evidence.

## Reproducing the measurements

```sh
uv run python tests/benchmark_pipeline.py --modules 2000 --repeats 3
uv run python tests/benchmark_pipeline.py --modules 2000 --repeats 3 --with-diagram
uv run python tests/benchmark_pipeline.py --modules 2000 --repeats 3 --compare-quadratic
```

The last command additionally times the reviewed prefix algorithm and verifies
that both helpers produce identical lengths. Its quadratic comparison may take
tens of seconds. `--case chain` and `--case cycle` exercise other graph shapes;
`--prefix-sizes` selects digest counts. The benchmark runs real CLI subprocesses
and validates the full module and fact inventory and the absence of a Mermaid
artifact in JSON-only mode.

The existing `tests/benchmark_quality.py` remains useful for isolating scoring
cost, while `tests/benchmark_pipeline.py` covers the full agent-facing path.
