# Function-local mutual imports

Moving imports into functions changes eager initialization, but leaves the
structural cycle intact. Every import statement counts, whether or not its
containing function executes.

The source root is `.`. This project is input to a static analyser; its modules are never imported by the corpus tests. See the central manifest for the expected structural outcome.
