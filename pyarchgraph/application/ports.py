"""Incoming and outgoing application contracts, with no concrete adapters."""

from pathlib import Path
from typing import Protocol

from pyarchgraph.domain.catalog import DiscoveryResult
from pyarchgraph.domain.location import (
    DirectoryLocation,
    ProjectLocation,
    ProjectMetadata,
)
from pyarchgraph.domain.model import (
    AnalysisOptions,
    AnalysisReport,
    FactCollection,
    SourceModule,
)


class ProjectAnalyzer(Protocol):
    def analyse(
        self,
        source_roots: tuple[Path, ...],
        *,
        options: AnalysisOptions,
        base_dir: Path | None = None,
    ) -> AnalysisReport: ...


class SourceDiscovery(Protocol):
    def discover(
        self,
        source_root: Path,
        *,
        excludes: tuple[str, ...] = (),
        pruned_directories: tuple[str, ...] = (),
    ) -> DiscoveryResult: ...


class ImportFactSource(Protocol):
    def collect(
        self, source_root: Path, modules: tuple[SourceModule, ...]
    ) -> FactCollection: ...


class ProjectAccess(Protocol):
    def locate(
        self, source_roots: tuple[Path, ...], base_dir: Path | None
    ) -> ProjectLocation: ...
    def relative(self, path: Path, base_dir: Path) -> str: ...
    def absolute(self, path: str, base_dir: Path) -> Path: ...
    def read_metadata(self, root: Path) -> ProjectMetadata: ...
    def directory(self, root: Path, candidate: str) -> DirectoryLocation: ...
