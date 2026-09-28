"""Immutable source facts, dependencies, findings, and analysis vocabulary."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

Details = Literal["summary", "component-edges"]
TargetKind = Literal["stub", "native", "generated", "excluded"]


class Severity(str, Enum):
    """Label how seriously a diagnostic or check result should be treated."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ImportSyntax(str, Enum):
    """Distinguish a plain import from a from-import statement."""

    IMPORT = "import"
    IMPORT_FROM = "import_from"


class ResolutionKind(str, Enum):
    """Describe how an import was matched to a module, base or probable submodule."""

    EXACT_MODULE = "exact_module"
    EXACT_BASE = "exact_base"
    PROBABLE_SUBMODULE = "probable_submodule"


class ExternalClassification(str, Enum):
    """Distinguish standard library imports from other imports outside the graph."""

    STDLIB = "stdlib"
    EXTERNAL_UNKNOWN = "external_unknown"


class UnresolvedReason(str, Enum):
    """Explain why an import could not be connected to an analysed source."""

    MISSING_INTERNAL_TARGET = "missing_internal_target"
    NAMESPACE_BASE_UNMODELLED = "namespace_base_unmodelled"
    RELATIVE_ESCAPE = "relative_escape"
    UNKNOWN_PACKAGE_CONTEXT = "unknown_package_context"
    AMBIGUOUS_TARGET = "ambiguous_target"


@dataclass(frozen=True, slots=True)
class SourceModule:
    """Identify a source file and record its import binding and analysis status."""

    id: str
    path: str
    is_package: bool
    parent_package: str | None
    import_name: str | None = None
    binding_status: Literal["bound", "path_only", "shadowed", "ambiguous"] = "bound"
    analysis_status: Literal["pending", "analyzed", "error"] = "pending"


@dataclass(frozen=True, slots=True)
class ImportContext:
    """Describe the surroundings of an import in source code for view filtering."""

    scope: Literal["module", "class", "function"] = "module"
    in_function: bool = False
    typing_only: bool = False
    conditional: bool = False
    exception_handler: bool = False
    package_initializer: bool = False


@dataclass(frozen=True, slots=True)
class ImportFactDraft:
    """Record one imported item before assigning its stable fact ID."""

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
    context: ImportContext = ImportContext()


@dataclass(frozen=True, slots=True)
class ImportFact:
    """Record one imported item with its source location, context and stable ID."""

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
    context: ImportContext = ImportContext()

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("import fact ID must be a nonempty string")


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """Record a concern about source scope, collection or resolution."""

    severity: Severity
    code: str
    message: str
    path: str | None = None
    line: int | None = None
    column: int | None = None


@dataclass(frozen=True, slots=True)
class DependencyEvidence:
    """Link a dependency to the import fact and resolution kind that support it."""

    fact_id: str
    resolution_kind: ResolutionKind


@dataclass(frozen=True, slots=True)
class DependencyEdge:
    """Represent an import dependency between source files, with supporting facts."""

    source: str
    target: str
    evidence: tuple[DependencyEvidence, ...]


@dataclass(frozen=True, slots=True)
class ExternalImport:
    """Record an import outside the source graph without claiming it is installed."""

    source: str
    requested: str
    classification: ExternalClassification
    fact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class UnresolvedImport:
    """Record an unmatched import, its reason and the facts that requested it."""

    source: str
    requested: str
    reason: UnresolvedReason
    fact_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvidenceLocation:
    """Locate supporting source text in a finding or target boundary.

    Report locations use base-relative paths and one-based character positions.
    """

    path: str
    line: int
    column: int
    source_segment: str | None
    resolution_kind: ResolutionKind | None
    context: ImportContext = ImportContext()
    source: str | None = None
    target: str | None = None
    fact_id: str | None = None


@dataclass(frozen=True, slots=True)
class TargetDeclaration:
    """Describe an unanalysed target and whether its coverage limit is accepted."""

    name: str
    kind: TargetKind
    reason: str
    path: str | None = None
    acknowledged: bool = False


@dataclass(frozen=True, slots=True)
class TargetBoundary:
    """Record an imported target outside analysis, with evidence and acknowledgement."""

    name: str
    kind: TargetKind
    path: str | None
    reason: str
    acknowledged: bool
    evidence: tuple[EvidenceLocation, ...]


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    """Collect resolved dependencies, other import outcomes and coverage concerns."""

    dependencies: tuple[DependencyEdge, ...]
    external_imports: tuple[ExternalImport, ...]
    unresolved_imports: tuple[UnresolvedImport, ...]
    diagnostics: tuple[Diagnostic, ...] = ()
    boundaries: tuple[TargetBoundary, ...] = ()


@dataclass(frozen=True, slots=True)
class FindingDependency:
    """Describe a dependency reported in a finding, with readable source evidence."""

    source: str
    target: str
    evidence: tuple[EvidenceLocation, ...]


@dataclass(frozen=True, slots=True)
class CycleFinding:
    """Describe one cyclic group, its certainty and a representative cycle."""

    certainty: Literal["definite", "possible"]
    members: tuple[str, ...]
    definite_members: tuple[str, ...]
    witness: tuple[FindingDependency, ...]
    dependency_count: int = 0
    dependencies: tuple[FindingDependency, ...] | None = None
    kind: Literal["cycle"] = field(default="cycle", init=False)


@dataclass(frozen=True, slots=True)
class ImportFinding:
    """Report a problem with an unresolved import and its supporting source evidence."""

    kind: Literal["unresolved_import"]
    source: str
    requested: str | None
    code: str
    message: str
    evidence: tuple[EvidenceLocation, ...]
    node: str | None = None


@dataclass(frozen=True, slots=True)
class RuleFinding:
    """Report a check's rule violation or observation about sources and view nodes."""

    code: str
    message: str
    node_ids: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    evidence: tuple[EvidenceLocation, ...] = ()
    kind: Literal["rule"] = field(default="rule", init=False)


Finding = CycleFinding | ImportFinding | RuleFinding


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Pair a finding with the severity assigned by its check."""

    severity: Severity
    finding: Finding
