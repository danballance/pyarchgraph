"""Immutable source catalog values shared across application ports."""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from pyarchgraph.domain.model import (
    Diagnostic,
    ExcludedPath,
    ImportFact,
    SourceModule,
    TargetDeclaration,
)


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    modules: tuple[SourceModule, ...]
    namespace_prefixes: tuple[str, ...]
    diagnostics: tuple[Diagnostic, ...]
    targets: tuple[TargetDeclaration, ...] = ()
    excluded_paths: tuple[ExcludedPath, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceCatalog:
    roots: tuple[Path, ...]
    modules: tuple[SourceModule, ...]
    facts: tuple[ImportFact, ...]
    diagnostics: tuple[Diagnostic, ...]
    targets: tuple[TargetDeclaration, ...] = ()
    excluded_paths: tuple[ExcludedPath, ...] = ()
    namespace_prefixes: tuple[str, ...] = ()
    effective_excludes: tuple[str, ...] = ()
