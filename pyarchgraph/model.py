"""Frozen domain objects for a syntactic Python import dependency analysis.

An edge ``A -> B`` means that module A contains import syntax that was
statically resolved to source-backed module B.  It does not claim that the
statement executes at runtime. Incomplete analysis raises an error instead of
producing a partial report.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


class Severity(str, Enum):
    ERROR = "error"


class ImportSyntax(str, Enum):
    IMPORT = "import"
    IMPORT_FROM = "import_from"


class ResolutionKind(str, Enum):
    EXACT_MODULE = "exact_module"
    EXACT_BASE = "exact_base"
    PROBABLE_SUBMODULE = "probable_submodule"


class ExternalClassification(str, Enum):
    STDLIB = "stdlib"
    EXTERNAL_UNKNOWN = "external_unknown"


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
    """Every unreadable or unsupported input has an error diagnostic."""

    facts: tuple[ImportFact, ...]
    diagnostics: tuple[Diagnostic, ...] = ()


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
class EvidenceLocation:
    """Self-contained evidence; paths are relative to cwd, positions one-based."""

    path: str
    line: int
    column: int
    source_segment: str | None
    resolution_kind: ResolutionKind | None


@dataclass(frozen=True, slots=True)
class FindingDependency:
    source: str
    target: str
    evidence: tuple[EvidenceLocation, ...]


@dataclass(frozen=True, slots=True)
class CycleFinding:
    certainty: Literal["definite", "possible"]
    members: tuple[str, ...]
    definite_members: tuple[str, ...]
    witness: tuple[FindingDependency, ...]
    kind: Literal["cycle"] = field(default="cycle", init=False)


@dataclass(frozen=True, slots=True)
class ForbiddenDependencyFinding:
    certainty: Literal["definite", "possible"]
    rules: tuple[tuple[str, str], ...]
    witness: tuple[FindingDependency, ...]
    kind: Literal["forbidden_dependency"] = field(
        default="forbidden_dependency", init=False
    )


@dataclass(frozen=True, slots=True)
class ImportFinding:
    kind: Literal["unresolved_import"]
    source: str
    requested: str | None
    code: str
    message: str
    evidence: tuple[EvidenceLocation, ...]


Finding = CycleFinding | ForbiddenDependencyFinding | ImportFinding


@dataclass(frozen=True, slots=True)
class AnalysisReport:
    """A completed analysis passes exactly when it has no findings."""

    module_count: int
    dependency_count: int
    findings: tuple[Finding, ...]
    schema_version: Literal["0.5"] = field(default="0.5", init=False)
