# Six direct edges with the same reachability

The fully ordered four-module DAG has twice the direct dependencies of
`layered_service`, but the same transitive reachability. Its six direct
dependencies remain acyclic, so it passes without findings.

The source root is `.`. This project is input to a static analyser; its modules are never imported by the corpus tests. See the central manifest for the expected structural outcome.
