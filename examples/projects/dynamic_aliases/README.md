# Aliased dynamic import functions

Module aliases and imported function aliases do not change the explicit-import
policy: dynamic calls produce no dependencies or findings. The explicit
`importlib` imports are still collected as external imports, and no internal
dependency is reported for the dynamic plugin target.

The source root is `.`. This project is input to a static analyser; its modules are never imported by the corpus tests. See the central manifest for the expected structural outcome.
