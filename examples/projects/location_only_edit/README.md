# Stable violation identity after whitespace edits

The before and after roots contain the same order/inventory dependencies. Comments and blank lines shift import locations and fact IDs, but must not create a new architectural violation in a baseline comparison.

The source root is `before`. This project is input to a static analyser; its modules are never imported by the corpus tests. See the central manifest for the expected structural outcome.
