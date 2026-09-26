# Type-only mutual references

The explicit imports in both `TYPE_CHECKING` blocks remain structural
dependencies and form a cycle. All import statements count, whether or not they
execute at runtime; annotations themselves are not interpreted.

The source root is `.`. This project is input to a static analyser; its modules are never imported by the corpus tests. See the central manifest for the expected structural outcome.
