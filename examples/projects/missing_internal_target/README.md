# A missing regular-package module

A source-backed package imports a nonexistent sibling. Parsing can be complete while internal dependency knowledge is incomplete, so missing targets must remain explicit.

The source root is `.`. This project is input to a static analyser; its modules are never imported by the corpus tests. See the central manifest for the expected structural outcome.
