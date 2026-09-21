# Cycle diluted by test consumers

One hundred test modules import the cyclic order module. Including them yields a high score; excluding tests restores the production score to zero without changing the cycle.

The source root is `.`. This project is input to a static analyser; its modules are never imported by the corpus tests. See the central manifest for the expected structural outcome.
