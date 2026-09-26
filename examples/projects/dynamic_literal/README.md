# Direct literal dynamic imports

Calls to `importlib.import_module()` and `__import__()` produce no dependencies
or findings, even with literal targets. The explicit `import loader` in
`plugin.py` still contributes `plugin -> loader`. The graph has no cycle because
the dynamic `loader -> plugin` relationship is outside pyarchgraph's coverage.

The source root is `.`. This project is input to a static analyser; its modules are never imported by the corpus tests. See the central manifest for the expected structural outcome.
