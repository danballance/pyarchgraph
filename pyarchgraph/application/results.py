"""Immutable results assembled by the analysis application."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from pyarchgraph.application.ports.sources import ExcludedPath
from pyarchgraph.domain.graph import ViewNode
from pyarchgraph.domain.models import (
    Diagnostic,
    EvidenceLocation,
    Finding,
    Severity,
    SourceModule,
    TargetBoundary,
)


@dataclass(frozen=True, slots=True)
class RegisteredFinding:
    """Connects a finding to the check that produced it and its severity."""

    check_id: str
    severity: Severity
    finding: Finding


@dataclass(frozen=True, slots=True)
class ReportDependency:
    """Describe a reported view dependency with readable original source evidence."""

    source: str
    target: str
    evidence: tuple[EvidenceLocation, ...]


@dataclass(frozen=True, slots=True)
class ViewReport:
    """Present nodes, counts, findings and optional full dependencies for one view."""

    nodes: tuple[ViewNode, ...]
    enabled_check_ids: tuple[str, ...]
    dependency_count: int
    cyclic_dependency_count: int
    cyclic_node_count: int
    findings: tuple[RegisteredFinding, ...]
    dependencies: tuple[ReportDependency, ...] | None = None


@dataclass(frozen=True, slots=True)
class Coverage:
    """Describes the analysed scope, exclusions, diagnostics and limits of an analysis."""

    roots: tuple[str, ...]
    excludes: tuple[str, ...]
    excluded_paths: tuple[ExcludedPath, ...]
    analyzed_source_count: int
    diagnostics: tuple[Diagnostic, ...]
    boundaries: tuple[TargetBoundary, ...]
    limitations: tuple[str, ...] = (
        "Only explicit import statements are analyzed; dynamic imports are outside coverage.",
        "External dependencies and runtime initialization are not analyzed.",
        "Package views group available sources; imports of namespace packages without a source target do not create package dependencies.",
    )


@dataclass(frozen=True, slots=True)
class AnalysisReport:
    """Presents analysis coverage and findings for every graph view.

    Completion concerns the declared scope, including accepted boundaries, not passing checks.
    """

    status: Literal["complete", "incomplete"]
    gate: str
    sources: tuple[SourceModule, ...]
    coverage: Coverage
    views: Mapping[str, ViewReport]
    schema_version: Literal["0.8"] = field(default="0.8", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "views", MappingProxyType(dict(self.views)))

    @property
    def selected_view(self) -> ViewReport:
        return self.views[self.gate]
