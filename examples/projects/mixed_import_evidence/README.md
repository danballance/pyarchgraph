# Ordinary and type-only evidence for one edge

One dependency has supporting imports at module level and in a `TYPE_CHECKING`
block. Both statements count and retain their evidence locations, while the
dependency and resulting cycle are counted once.

The source root is `.`. This project is input to a static analyser; its modules are never imported by the corpus tests. See the central manifest for the expected structural outcome.
