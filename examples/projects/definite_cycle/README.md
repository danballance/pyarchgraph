# Order and inventory cycle

Two exact module imports form a definite cycle. The cycle must remain a finding regardless of unrelated connected code or tests.

The source root is `.`. This project is input to a static analyser; its modules are never imported by the corpus tests. See the central manifest for the expected structural outcome.
