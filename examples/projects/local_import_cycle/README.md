# Function-local mutual imports

Moving imports into functions changes eager initialization, but leaves the structural cycle intact. An eager-import view can be separate from the architecture view.

The source root is `.`. This project is input to a static analyser; its modules are never imported by the corpus tests. See the central manifest for the expected structural outcome.
