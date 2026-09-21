# A source-layout cycle and its incorrect root

The declared import root is src. Analysing the repository instead changes pkg.* identities to src.pkg.* and can hide the real cycle behind external-unknown imports. Validate expected package names before using the score.

The source root is `src`. This project is input to a static analyser; its modules are never imported by the corpus tests. See the central manifest for the expected structural outcome.
