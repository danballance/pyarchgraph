"""Frozen domain objects for a syntactic Python import dependency analysis.

An edge ``A -> B`` means that module A contains import syntax that was
statically resolved to source-backed module B.  It does not claim that the
statement executes at runtime.  An analysis is complete exactly when it has
no error diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class ImportSyntax(str, Enum):
    IMPORT = "import"
    IMPORT_FROM = "import_from"


class ImportScope(str, Enum):
    MODULE = "module"
    LOCAL = "local"


class ResolutionKind(str, Enum):
    EXACT_MODULE = "exact_module"
    EXACT_BASE = "exact_base"
    PROBABLE_SUBMODULE = "probable_submodule"


class ExternalClassification(str, Enum):
    STDLIB = "stdlib"
    EXTERNAL_UNKNOWN = "external_unknown"


class View(str, Enum):
    """Which graph the condensation DAG and diagram describe.

    ``MODULE`` is the analysis's own grain: one node per source module.
    ``PACKAGE`` projects those modules onto a package prefix before
    condensing, which is a presentation choice — the module-level evidence in
    an ``AnalysisResult`` is unchanged by it.
    """

    MODULE = "module"
    PACKAGE = "package"


class UnresolvedReason(str, Enum):
    MISSING_INTERNAL_TARGET = "missing_internal_target"
    NAMESPACE_BASE_UNMODELLED = "namespace_base_unmodelled"
    RELATIVE_ESCAPE = "relative_escape"


@dataclass(frozen=True, slots=True)
class SourceModule:
    id: str
    path: str
    is_package: bool
    parent_package: str | None


@dataclass(frozen=True, slots=True)
class SourceLocation:
    path: str
    line: int
    column: int
    end_line: int | None
    end_column: int | None


@dataclass(frozen=True, slots=True)
class ImportFact:
    id: str
    source: str
    path: str
    line: int
    column: int
    end_line: int | None
    end_column: int | None
    alias_index: int
    syntax: ImportSyntax
    source_segment: str | None
    base_module: str | None
    imported_name: str | None
    as_name: str | None
    bound_name: str
    relative_level: int
    scope: ImportScope
    type_only: bool


@dataclass(frozen=True, slots=True)
class Diagnostic:
    severity: Severity
    code: str
    message: str
    path: str | None = None
    line: int | None = None
    column: int | None = None


@dataclass(frozen=True, slots=True)
class FactCollection:
    """Facts and diagnostics produced for a complete module inventory.

    Returning a collection with no error diagnostics asserts that every given
    module was processed successfully.  A fact source must emit an error for
    each read, decode, parse, or equivalent indexing failure; otherwise the
    orchestrator cannot truthfully calculate ``AnalysisResult.complete``.
    """

    facts: tuple[ImportFact, ...]
    diagnostics: tuple[Diagnostic, ...] = ()


class ImportFactSource(Protocol):
    """Repository-level seam for collecting source-backed import facts.

    Implementations receive the entire unambiguous inventory and must report
    every processing failure as an error in the returned ``FactCollection``.
    """

    def collect(
        self,
        source_root: Path,
        modules: tuple[SourceModule, ...],
    ) -> FactCollection: ...


@dataclass(frozen=True, slots=True)
class ResolvedImport:
    source: str
    target: str
    fact_id: str
    resolution_kind: ResolutionKind


@dataclass(frozen=True, slots=True)
class DependencyEvidence:
    fact_id: str
    resolution_kind: ResolutionKind


@dataclass(frozen=True, slots=True)
class DependencyEdge:
    source: str
    target: str
    evidence: tuple[DependencyEvidence, ...]


@dataclass(frozen=True, slots=True)
class ExternalImport:
    source: str
    requested: str
    classification: ExternalClassification
    fact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UnresolvedImport:
    source: str
    requested: str
    reason: UnresolvedReason
    fact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    dependencies: tuple[DependencyEdge, ...]
    external_imports: tuple[ExternalImport, ...]
    unresolved_imports: tuple[UnresolvedImport, ...]


@dataclass(frozen=True, slots=True)
class GraphNode:
    id: str


@dataclass(frozen=True, slots=True)
class RawDependency:
    source: str
    target: str


@dataclass(frozen=True, slots=True)
class DagNode:
    id: str
    members: tuple[str, ...]
    cyclic: bool


@dataclass(frozen=True, slots=True)
class DagEdge:
    source: str
    target: str
    raw_dependencies: tuple[RawDependency, ...]


@dataclass(frozen=True, slots=True)
class Dag:
    nodes: tuple[DagNode, ...]
    edges: tuple[DagEdge, ...]
    dependency_first_layers: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """One analysis, its provenance, and the graph derived from it.

    ``excludes``, ``view`` and ``package_depth`` are recorded so a written
    artifact states what it covered: without them a consumer cannot tell an
    excluded package from an absent one. The source root is deliberately not
    recorded — every path here is relative to it, which is what keeps an
    artifact portable between machines.
    """

    complete: bool
    python_version: str
    excludes: tuple[str, ...]
    view: View
    package_depth: int | None
    namespace_prefixes: tuple[str, ...]
    modules: tuple[SourceModule, ...]
    import_facts: tuple[ImportFact, ...]
    dependencies: tuple[DependencyEdge, ...]
    external_imports: tuple[ExternalImport, ...]
    unresolved_imports: tuple[UnresolvedImport, ...]
    dag: Dag
    diagnostics: tuple[Diagnostic, ...]
