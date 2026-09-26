# Configuration-selected dynamic imports

Runtime-selected plugin calls produce no dependencies or findings. The explicit
`importlib` imports are still collected as external imports. Dependencies
introduced solely by the dynamic calls are outside pyarchgraph's coverage.

The source root is `.`. This project is input to a static analyser; its modules are never imported by the corpus tests. See the central manifest for the expected structural outcome.
