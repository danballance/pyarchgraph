"""Typed, deterministic reports for scoped explicit-import analysis."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING
from enum import Enum
from typing import Literal

if TYPE_CHECKING:
    from pyarchgraph.domain.graph import ViewNode

Gate = str
Details = Literal["summary", "component-edges"]
TargetKind = Literal["stub", "native", "generated", "excluded"]


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


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
    UNKNOWN_PACKAGE_CONTEXT = "unknown_package_context"
    AMBIGUOUS_TARGET = "ambiguous_target"


@dataclass(frozen=True, slots=True)
class SourceModule:
    id: str
    path: str
    is_package: bool
    parent_package: str | None
    import_name: str | None = None
    binding_status: Literal["bound", "path_only", "shadowed", "ambiguous"] = "bound"
    analysis_status: Literal["pending", "analyzed", "error"] = "pending"


@dataclass(frozen=True, slots=True)
class ImportContext:
    scope: Literal["module", "class", "function"] = "module"
    in_function: bool = False
    typing_only: bool = False
    conditional: bool = False
    exception_handler: bool = False
    package_initializer: bool = False


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
    context: ImportContext = ImportContext()


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
class EvidenceLocation:
    """Report locations use base-relative paths and one-based character positions."""

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
    name: str
    kind: TargetKind
    reason: str
    path: str | None = None
    acknowledged: bool = False


@dataclass(frozen=True, slots=True)
class TargetBoundary:
    name: str
    kind: TargetKind
    path: str | None
    reason: str
    acknowledged: bool
    evidence: tuple[EvidenceLocation, ...]


@dataclass(frozen=True, slots=True)
class ResolutionResult:
    dependencies: tuple[DependencyEdge, ...]
    external_imports: tuple[ExternalImport, ...]
    unresolved_imports: tuple[UnresolvedImport, ...]
    diagnostics: tuple[Diagnostic, ...] = ()
    boundaries: tuple[TargetBoundary, ...] = ()


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
    dependency_count: int = 0
    dependencies: tuple[FindingDependency, ...] | None = None
    kind: Literal["cycle"] = field(default="cycle", init=False)


@dataclass(frozen=True, slots=True)
class ImportFinding:
    kind: Literal["unresolved_import"]
    source: str
    requested: str | None
    code: str
    message: str
    evidence: tuple[EvidenceLocation, ...]
    node: str | None = None


@dataclass(frozen=True, slots=True)
class RuleFinding:
    code: str
    message: str
    node_ids: tuple[str, ...] = ()
    source_ids: tuple[str, ...] = ()
    evidence: tuple[EvidenceLocation, ...] = ()
    kind: Literal["rule"] = field(default="rule", init=False)


Finding = CycleFinding | ImportFinding | RuleFinding


@dataclass(frozen=True, slots=True)
class CheckResult:
    severity: Severity
    finding: Finding


@dataclass(frozen=True, slots=True)
class RegisteredFinding:
    check_id: str
    severity: Severity
    finding: Finding


@dataclass(frozen=True, slots=True)
class GraphView:
    nodes: tuple[ViewNode, ...]
    enabled_check_ids: tuple[str, ...]
    dependency_count: int
    cyclic_dependency_count: int
    cyclic_node_count: int
    findings: tuple[RegisteredFinding, ...]


@dataclass(frozen=True, slots=True)
class ExcludedPath:
    path: str
    rule: str
    kind: Literal["file", "directory"]


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
class AnalysisOptions:
    excludes: tuple[str, ...] = ()
    gate: Gate = "structural"
    details: Details = "summary"
    owned_prefixes: tuple[str, ...] = ()
    targets: tuple[TargetDeclaration, ...] = ()


@dataclass(frozen=True, slots=True)
class AnalysisReport:
    """Completion concerns the declared scope, including accepted boundaries."""

    status: Literal["complete", "incomplete"]
    gate: Gate
    sources: tuple[SourceModule, ...]
    coverage: Coverage
    views: Mapping[str, GraphView]
    schema_version: Literal["0.7"] = field(default="0.7", init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "views", MappingProxyType(dict(self.views)))

    @property
    def selected_view(self) -> GraphView:
        return self.views[self.gate]

    @property
    def exit_code(self) -> int:
        return (
            2
            if self.status == "incomplete"
            else int(
                any(
                    item.severity is Severity.ERROR
                    for item in self.selected_view.findings
                )
            )
        )
