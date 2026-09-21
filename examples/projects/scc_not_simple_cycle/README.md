# A three-module component with two-node cycles

Editor and history each depend on events, while events depends on both. The SCC has three modules, but the longest simple cycle has two: component size must not be labelled cycle length.

The source root is `.`. This project is input to a static analyser; its modules are never imported by the corpus tests. See the central manifest for the expected structural outcome.
