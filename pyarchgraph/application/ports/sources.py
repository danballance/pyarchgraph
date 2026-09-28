"""Source collection ports and their immutable application observations."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

from pyarchgraph.domain.models import (
    Diagnostic,
    ImportFactDraft,
    SourceModule,
    TargetDeclaration,
)


@dataclass(frozen=True, slots=True)
class ExcludedPath:
    path: str
    rule: str
    kind: Literal["file", "directory"]


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    modules: tuple[SourceModule, ...]
    namespace_prefixes: tuple[str, ...]
    diagnostics: tuple[Diagnostic, ...]
    targets: tuple[TargetDeclaration, ...] = ()
    excluded_paths: tuple[ExcludedPath, ...] = ()


@dataclass(frozen=True, slots=True)
class FactCollection:
    facts: tuple[ImportFactDraft, ...]
    diagnostics: tuple[Diagnostic, ...] = ()


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
