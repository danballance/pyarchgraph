"""Immutable inputs to project analysis; validated before any external access."""

from dataclasses import dataclass
from pathlib import Path

from pyarchgraph.domain.models import Details, TargetDeclaration


@dataclass(frozen=True, slots=True)
class AnalysisOptions:
    """Selects the analysis scope, gate view and level of report detail."""

    excludes: tuple[str, ...] = ()
    gate: str = "structural"
    details: Details = "summary"
    owned_prefixes: tuple[str, ...] = ()
    targets: tuple[TargetDeclaration, ...] = ()


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    """Describes the source roots and options for one project analysis."""

    source_roots: tuple[Path, ...]
    options: AnalysisOptions = AnalysisOptions()
    base_dir: Path | None = None
