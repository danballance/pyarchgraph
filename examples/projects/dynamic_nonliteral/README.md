# Configuration-selected dynamic imports

A runtime-selected plugin name cannot be resolved from its call expression. Both module and function aliases need a visible warning and must not claim exhaustive dependency knowledge.

The source root is `.`. This project is input to a static analyser; its modules are never imported by the corpus tests. See the central manifest for the expected structural outcome.
