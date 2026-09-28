"""Project access port and immutable filesystem observations."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ProjectLocation:
    """Identifies the import roots and shared base directory for an analysis."""

    base_dir: Path
    roots: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class ProjectMetadata:
    """Carries packaging clues used to assess whether import roots match the project layout."""

    root_candidates: tuple[str, ...] = ()
    package_aliases: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class DirectoryLocation:
    """Records the location and existence of a possible import root."""

    path: Path
    exists: bool


class ProjectAccess(Protocol):
    """Provides project locations and layout observations to the analysis workflow."""

    def locate(
        self, source_roots: tuple[Path, ...], base_dir: Path | None
    ) -> ProjectLocation: ...
    def relative(self, path: Path, base_dir: Path) -> str: ...
    def absolute(self, path: str, base_dir: Path) -> Path: ...
    def read_metadata(self, root: Path) -> ProjectMetadata: ...
    def directory(self, root: Path, candidate: str) -> DirectoryLocation: ...
