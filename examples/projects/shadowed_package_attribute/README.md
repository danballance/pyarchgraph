# An attribute shadows a child-module candidate

pkg defines b = 42, so from pkg import b can consume a package attribute despite a same-named source module. A candidate edge may suggest a cycle, but cannot justify a definite cycle failure.

The source root is `.`. This project is input to a static analyser; its modules are never imported by the corpus tests. See the central manifest for the expected structural outcome.
