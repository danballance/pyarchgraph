"""Shared repository context, independent of individual test directory depth."""

from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def repository_root() -> Path:
    return Path(__file__).resolve().parent.parent
