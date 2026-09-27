"""Thin bootstrap for the console script and ``python -m pyarchgraph``."""

from collections.abc import Sequence

from pyarchgraph.composition import ApplicationFactory


def main(argv: Sequence[str] | None = None) -> int:
    return ApplicationFactory().create_cli().run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
