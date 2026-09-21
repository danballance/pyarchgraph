# Equivalent package imports: relative

The package re-exports a, and a uses b.VALUE. Relative from-import and absolute submodule spelling must yield the same architectural dependencies and score. Package initialization syntax is still retained as evidence.

The source root is `.`. This project is input to a static analyser; its modules are never imported by the corpus tests. See the central manifest for the expected structural outcome.
