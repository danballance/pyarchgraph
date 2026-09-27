"""Immutable project locations and metadata observations."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProjectLocation:
    base_dir: Path
    roots: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class ProjectMetadata:
    root_candidates: tuple[str, ...] = ()
    package_aliases: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class DirectoryLocation:
    path: Path
    exists: bool
