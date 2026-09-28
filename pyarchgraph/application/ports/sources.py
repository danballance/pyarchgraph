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
    """Records a path left out of source discovery and the rule that excluded it."""

    path: str
    rule: str
    kind: Literal["file", "directory"]


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """Collects the sources, possible import targets and scope observations found in one root."""

    modules: tuple[SourceModule, ...]
    namespace_prefixes: tuple[str, ...]
    diagnostics: tuple[Diagnostic, ...]
    targets: tuple[TargetDeclaration, ...] = ()
    excluded_paths: tuple[ExcludedPath, ...] = ()


@dataclass(frozen=True, slots=True)
class FactCollection:
    """Carries import drafts and diagnostics collected from source files."""

    facts: tuple[ImportFactDraft, ...]
    diagnostics: tuple[Diagnostic, ...] = ()


class SourceDiscovery(Protocol):
    """Defines how the application discovers sources and import targets within a root."""

    def discover(
        self,
        source_root: Path,
        *,
        excludes: tuple[str, ...] = (),
        pruned_directories: tuple[str, ...] = (),
    ) -> DiscoveryResult: ...


class ImportFactSource(Protocol):
    """Defines how the application obtains import drafts from discovered sources."""

    def collect(
        self, source_root: Path, modules: tuple[SourceModule, ...]
    ) -> FactCollection: ...
