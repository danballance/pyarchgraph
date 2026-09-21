# Ordinary and type-only evidence for one edge

One dependency has both ordinary and type-only supporting imports. Filtering type-only facts must keep the ordinary edge and the resulting cycle.

The source root is `.`. This project is input to a static analyser; its modules are never imported by the corpus tests. See the central manifest for the expected structural outcome.
