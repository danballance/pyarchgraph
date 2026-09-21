# A public package re-export

The application consumes a public class through a package initializer. Keeping app -> toolkit and toolkit -> toolkit.models preserves a real dependency; removing every package edge would erase it.

The source root is `.`. This project is input to a static analyser; its modules are never imported by the corpus tests. See the central manifest for the expected structural outcome.
