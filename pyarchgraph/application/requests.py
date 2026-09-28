"""Immutable inputs to project analysis; validated before any external access."""

from dataclasses import dataclass
from pathlib import Path

from pyarchgraph.domain.models import Details, TargetDeclaration


@dataclass(frozen=True, slots=True)
class AnalysisOptions:
    excludes: tuple[str, ...] = ()
    gate: str = "structural"
    details: Details = "summary"
    owned_prefixes: tuple[str, ...] = ()
    targets: tuple[TargetDeclaration, ...] = ()


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    source_roots: tuple[Path, ...]
    options: AnalysisOptions = AnalysisOptions()
    base_dir: Path | None = None
