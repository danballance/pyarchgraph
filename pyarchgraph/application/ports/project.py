"""Project access port and immutable filesystem observations."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


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


class ProjectAccess(Protocol):
    def locate(
        self, source_roots: tuple[Path, ...], base_dir: Path | None
    ) -> ProjectLocation: ...
    def relative(self, path: Path, base_dir: Path) -> str: ...
    def absolute(self, path: str, base_dir: Path) -> Path: ...
    def read_metadata(self, root: Path) -> ProjectMetadata: ...
    def directory(self, root: Path, candidate: str) -> DirectoryLocation: ...
