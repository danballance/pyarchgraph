# A valid namespace-package import

There are deliberately no __init__.py files. The reader is a real namespace-package submodule; an unmodelled namespace base is different from a missing internal target.

The source root is `.`. This project is input to a static analyser; its modules are never imported by the corpus tests. See the central manifest for the expected structural outcome.
