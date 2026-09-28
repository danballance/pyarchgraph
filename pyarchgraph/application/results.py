"""Immutable results assembled by the analysis application."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from pyarchgraph.application.ports.sources import ExcludedPath
from pyarchgraph.domain.graph import ViewNode
from pyarchgraph.domain.models import (
    Diagnostic,
    Finding,
    Severity,
    SourceModule,
    TargetBoundary,
)


@dataclass(frozen=True, slots=True)
class RegisteredFinding:
    check_id: str
    severity: Severity
    finding: Finding


@dataclass(frozen=True, slots=True)
class ViewReport:
    nodes: tuple[ViewNode, ...]
    enabled_check_ids: tuple[str, ...]
    dependency_count: int
    cyclic_dependency_count: int
    cyclic_node_count: int
    findings: tuple[RegisteredFinding, ...]


@dataclass(frozen=True, slots=True)
class Coverage:
    roots: tuple[str, ...]
    excludes: tuple[str, ...]
    excluded_paths: tuple[ExcludedPath, ...]
    analyzed_source_count: int
    diagnostics: tuple[Diagnostic, ...]
    boundaries: tuple[TargetBoundary, ...]
    limitations: tuple[str, ...] = (
        "Only explicit import statements are analyzed; dynamic imports are outside coverage.",
        "External dependencies and runtime initialization are not analyzed.",
    )


@dataclass(frozen=True, slots=True)
class AnalysisReport:
    """Completion concerns the declared scope, including accepted boundaries."""

    status: Literal["complete", "incomplete"]
    gate: str
    sources: tuple[SourceModule, ...]
    coverage: Coverage
    views: Mapping[str, ViewReport]
    schema_version: Literal["0.7"] = field(default="0.7", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "views", MappingProxyType(dict(self.views)))

    @property
    def selected_view(self) -> ViewReport:
        return self.views[self.gate]
