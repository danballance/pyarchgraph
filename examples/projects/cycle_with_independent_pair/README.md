# Cycle plus an unrelated feature

Adding an unrelated receipt-to-currency dependency raises the score from 0 to 57.5 while leaving the same order cycle untouched. Removing that dependency lowers the score despite removing coupling.

The source root is `.`. This project is input to a static analyser; its modules are never imported by the corpus tests. See the central manifest for the expected structural outcome.
