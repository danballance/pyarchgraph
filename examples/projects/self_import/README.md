# Registry imports itself

A module importing itself is a cyclic component of size one and needs a bounded witness.

The source root is `.`. This project is input to a static analyser; its modules are never imported by the corpus tests. See the central manifest for the expected structural outcome.
