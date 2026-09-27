"""Resolve explicit imports against declared source identities and boundaries."""

from __future__ import annotations

import importlib.util
import sys
from collections import defaultdict
from dataclasses import dataclass, replace
from collections.abc import Mapping
from types import MappingProxyType
from pathlib import PurePosixPath

from pyarchgraph.domain.model import (
    DependencyEdge,
    DependencyEvidence,
    Diagnostic,
    EvidenceLocation,
    ExternalClassification,
    ExternalImport,
    ImportFact,
    ImportSyntax,
    ResolutionKind,
    ResolutionResult,
    Severity,
    SourceModule,
    TargetBoundary,
    TargetDeclaration,
    UnresolvedImport,
    UnresolvedReason,
)


@dataclass(frozen=True, slots=True)
class BindingIndex:
    """A call's immutable import-name bindings and declared target boundaries."""

    by_id: Mapping[str, SourceModule]
    names: Mapping[str, tuple[SourceModule, ...]]
    ambiguous: frozenset[str]
    bound: Mapping[str, SourceModule]
    owned: frozenset[str]
    namespaces: frozenset[str]
    target_groups: Mapping[str, tuple[TargetDeclaration, ...]]
    targets: tuple[TargetDeclaration, ...]
    diagnostics: tuple[Diagnostic, ...]

    @classmethod
    def build(
        cls,
        modules: tuple[SourceModule, ...],
        namespace_prefixes: tuple[str, ...],
        *,
        owned_prefixes: tuple[str, ...] = (),
        targets: tuple[TargetDeclaration, ...] = (),
    ) -> BindingIndex:
        by_id = {module.id: module for module in modules}
        names: defaultdict[str, list[SourceModule]] = defaultdict(list)
        for module in modules:
            if module.import_name and module.binding_status in {"bound", "ambiguous"}:
                names[module.import_name].append(module)
        ambiguous = {
            name
            for name, candidates in names.items()
            if len(candidates) != 1
            or any((item.binding_status == "ambiguous" for item in candidates))
        }
        bound = {
            name: candidates[0]
            for name, candidates in names.items()
            if name not in ambiguous
        }
        owned = frozenset((*owned_prefixes, *names))
        # Namespace ancestors do not claim absent siblings as internal sources.
        namespaces = frozenset(namespace_prefixes) - frozenset(names)
        target_groups: defaultdict[str, list[TargetDeclaration]] = defaultdict(list)
        for target in targets:
            target_groups[target.name].append(target)
        native_ambiguities = []
        # Companion stubs/build inputs can coexist; competing implementations
        # cannot become unambiguous merely by acknowledging incomplete coverage.
        for name, declarations in sorted(target_groups.items()):
            native = [item for item in declarations if item.kind == "native"]
            native_locations = {
                str(PurePosixPath(item.path).parent) for item in native if item.path
            }
            if len(native_locations) > 1 or (
                name in bound and any((cls._native_artifact(item) for item in native))
            ):
                ambiguous.add(name)
                native_ambiguities.append(
                    Diagnostic(
                        Severity.ERROR,
                        "ambiguous_target",
                        f"Import name {name!r} has competing Python/native implementations.",
                        sorted((item.path for item in native if item.path))[0],
                    )
                )
        return cls(
            MappingProxyType(by_id),
            MappingProxyType({name: tuple(items) for name, items in names.items()}),
            frozenset(ambiguous),
            MappingProxyType(bound),
            owned,
            namespaces,
            MappingProxyType(
                {name: tuple(items) for name, items in target_groups.items()}
            ),
            targets,
            tuple(native_ambiguities),
        )

    @staticmethod
    def _native_artifact(target: TargetDeclaration) -> bool:
        return target.kind == "native" and bool(
            target.path and target.path.endswith((".so", ".pyd"))
        )


class _ResolutionSession:
    """Accumulate one resolution without retaining mutable results on services."""

    def __init__(self, index: BindingIndex) -> None:
        self.index = index
        self.dependency_evidence: defaultdict[
            tuple[str, str], list[DependencyEvidence]
        ] = defaultdict(list)
        self.external_ids: defaultdict[
            tuple[str, str, ExternalClassification], list[str]
        ] = defaultdict(list)
        self.unresolved_ids: defaultdict[
            tuple[str, str, UnresolvedReason], list[str]
        ] = defaultdict(list)
        self.boundary_facts: defaultdict[TargetDeclaration, list[ImportFact]] = (
            defaultdict(list)
        )
        self.diagnostics: list[Diagnostic] = list(index.diagnostics)

    def resolve(self, facts: tuple[ImportFact, ...]) -> ResolutionResult:
        for fact in facts:
            if fact.syntax is ImportSyntax.IMPORT:
                assert fact.base_module is not None
                if not self.dependency(
                    fact, fact.base_module, ResolutionKind.EXACT_MODULE
                ):
                    self.classify_absent(fact, fact.base_module)
                continue
            absolute_base = fact.base_module or ""
            if fact.relative_level:
                requested = "." * fact.relative_level + absolute_base
                source = self.index.by_id.get(fact.source)
                if (
                    source is None
                    or source.binding_status == "path_only"
                    or source.import_name is None
                ):
                    self.unresolved(
                        fact, requested, UnresolvedReason.UNKNOWN_PACKAGE_CONTEXT
                    )
                    continue
                package = (
                    source.import_name if source.is_package else source.parent_package
                )
                if not package:
                    self.unresolved(fact, requested, UnresolvedReason.RELATIVE_ESCAPE)
                    continue
                try:
                    absolute_base = importlib.util.resolve_name(requested, package)
                except ImportError:
                    self.unresolved(fact, requested, UnresolvedReason.RELATIVE_ESCAPE)
                    continue
            module = self.index.bound.get(absolute_base)
            # Only an initializer's own-package base is redundant. Ordinary
            # module from-self imports retain their explicit self-dependency.
            suppress_initializer = (
                module is not None and module.is_package and (fact.source == module.id)
            )
            if not suppress_initializer and (
                not self.dependency(fact, absolute_base, ResolutionKind.EXACT_BASE)
            ):
                self.classify_absent(fact, absolute_base)
            imported = fact.imported_name
            if imported and imported != "*":
                candidate = f"{absolute_base}.{imported}"
                if candidate in self.index.names:
                    self.dependency(fact, candidate, ResolutionKind.PROBABLE_SUBMODULE)
                elif (
                    self.target_for(candidate) is not None
                    or absolute_base in self.index.namespaces
                ):
                    self.classify_absent(fact, candidate)
        dependencies = tuple(
            (
                DependencyEdge(
                    source,
                    target,
                    tuple(
                        sorted(
                            set(evidence),
                            key=lambda item: (item.fact_id, item.resolution_kind.value),
                        )
                    ),
                )
                for (source, target), evidence in sorted(
                    self.dependency_evidence.items()
                )
            )
        )
        external_imports = tuple(
            (
                ExternalImport(
                    source, requested, classification, tuple(sorted(set(ids)))
                )
                for (source, requested, classification), ids in sorted(
                    self.external_ids.items(),
                    key=lambda item: (item[0][0], item[0][1], item[0][2].value),
                )
            )
        )
        unresolved_imports = tuple(
            (
                UnresolvedImport(source, requested, reason, tuple(sorted(set(ids))))
                for (source, requested, reason), ids in sorted(
                    self.unresolved_ids.items(),
                    key=lambda item: (item[0][0], item[0][1], item[0][2].value),
                )
            )
        )
        boundaries = tuple(
            (
                TargetBoundary(
                    declaration.name,
                    declaration.kind,
                    declaration.path,
                    declaration.reason,
                    declaration.acknowledged,
                    tuple(
                        sorted(
                            {self._location(fact) for fact in evidence},
                            key=lambda item: (
                                item.path,
                                item.line,
                                item.column,
                                item.source_segment or "",
                                repr(item.context),
                            ),
                        )
                    ),
                )
                for declaration, evidence in sorted(
                    self.boundary_facts.items(),
                    key=lambda item: (item[0].name, item[0].kind, item[0].path or ""),
                )
            )
        )
        return ResolutionResult(
            dependencies,
            external_imports,
            unresolved_imports,
            tuple(
                sorted(
                    set(self.diagnostics),
                    key=lambda item: (
                        item.path or "",
                        item.line or 0,
                        item.column or 0,
                        item.code,
                        item.message,
                    ),
                )
            ),
            boundaries,
        )

    def unresolved(
        self, fact: ImportFact, requested: str, reason: UnresolvedReason
    ) -> None:
        self.unresolved_ids[fact.source, requested, reason].append(fact.id)
        if reason in {
            UnresolvedReason.AMBIGUOUS_TARGET,
            UnresolvedReason.UNKNOWN_PACKAGE_CONTEXT,
        }:
            message = (
                f"Import {requested!r} has more than one possible source or native binding."
                if reason is UnresolvedReason.AMBIGUOUS_TARGET
                else f"Relative import {requested!r} has no established package context for this path-only source."
            )
            self.diagnostics.append(
                Diagnostic(
                    Severity.ERROR,
                    reason.value,
                    message,
                    fact.path,
                    fact.line,
                    fact.column + 1,
                )
            )

    def target_for(self, requested: str) -> TargetDeclaration | None:
        declarations = list(self.index.target_groups.get(requested, ()))
        if not declarations:
            # Excluded directories establish prefix boundaries without traversal.
            candidates = [
                item
                for item in self.index.targets
                if item.kind == "excluded"
                and item.path
                and item.path.endswith("/")
                and self._within(requested, item.name)
            ]
            if candidates:
                longest = max((len(item.name) for item in candidates))
                declarations = [
                    item for item in candidates if len(item.name) == longest
                ]
        if not declarations:
            return None
        kinds = {item.kind for item in declarations}
        # Stubs may accompany native/generated implementations. Merge explicit
        # acknowledgements without weakening the selected availability kind.
        kind = next(
            (
                item
                for item in ("native", "generated", "stub", "excluded")
                if item in kinds
            )
        )
        selected = sorted(
            (item for item in declarations if item.kind == kind),
            key=lambda item: (item.path is None, item.path or "", item.reason),
        )[0]
        acknowledged = any(
            (item.acknowledged for item in declarations if item.kind == kind)
        )
        reasons = sorted({item.reason for item in declarations if item.kind == kind})
        return replace(
            selected,
            name=requested,
            acknowledged=acknowledged,
            reason=" ".join(reasons),
        )

    def classify_absent(self, fact: ImportFact, requested: str) -> None:
        if any(
            (prefix in self.index.ambiguous for prefix in self._prefixes(requested))
        ):
            self.unresolved(fact, requested, UnresolvedReason.AMBIGUOUS_TARGET)
            return
        if declaration := self.target_for(requested):
            self.boundary_facts[declaration].append(fact)
            return
        if requested in self.index.namespaces:
            self.unresolved(fact, requested, UnresolvedReason.NAMESPACE_BASE_UNMODELLED)
            return
        if any((prefix in self.index.owned for prefix in self._prefixes(requested))):
            self.unresolved(fact, requested, UnresolvedReason.MISSING_INTERNAL_TARGET)
        else:
            classification = (
                ExternalClassification.STDLIB
                if requested.partition(".")[0] in sys.stdlib_module_names
                else ExternalClassification.EXTERNAL_UNKNOWN
            )
            self.external_ids[fact.source, requested, classification].append(fact.id)

    def dependency(
        self, fact: ImportFact, requested: str, kind: ResolutionKind
    ) -> bool:
        if any(
            (prefix in self.index.ambiguous for prefix in self._prefixes(requested))
        ):
            self.unresolved(fact, requested, UnresolvedReason.AMBIGUOUS_TARGET)
            return True
        if module := self.index.bound.get(requested):
            self.dependency_evidence[fact.source, module.id].append(
                DependencyEvidence(fact.id, kind)
            )
            return True
        return False

    def _within(self, name: str, prefix: str) -> bool:
        return name == prefix or name.startswith(prefix + ".")

    def _prefixes(self, name: str) -> tuple[str, ...]:
        parts = name.split(".")
        return tuple((".".join(parts[:index]) for index in range(1, len(parts) + 1)))

    def _location(self, fact: ImportFact) -> EvidenceLocation:
        return EvidenceLocation(
            fact.path,
            fact.line,
            fact.column + 1,
            fact.source_segment,
            None,
            fact.context,
        )


class StaticImportResolver:
    """Resolve facts from declared bindings without inspecting installed packages."""

    def resolve(
        self,
        facts: tuple[ImportFact, ...],
        modules: tuple[SourceModule, ...],
        namespace_prefixes: tuple[str, ...] = (),
        *,
        owned_prefixes: tuple[str, ...] = (),
        targets: tuple[TargetDeclaration, ...] = (),
    ) -> ResolutionResult:
        index = BindingIndex.build(
            modules, namespace_prefixes, owned_prefixes=owned_prefixes, targets=targets
        )
        return _ResolutionSession(index).resolve(facts)


class ArchitectureDependencyPolicy:
    """Select source-backed architecture relations before view filtering."""

    def select(
        self, dependencies: tuple[DependencyEdge, ...]
    ) -> tuple[DependencyEdge, ...]:
        """Prefer each fact's probable child over its redundant exact package base.

        This is a fact-level selection: source IDs are opaque, and do not encode
        dotted import parents. Independent evidence for the package remains.
        Apply before filtering evidence by resolution certainty or import context.
        """
        replaced = {
            (edge.source, evidence.fact_id)
            for edge in dependencies
            for evidence in edge.evidence
            if evidence.resolution_kind is ResolutionKind.PROBABLE_SUBMODULE
        }
        selected = []
        for edge in dependencies:
            evidence = tuple(
                (
                    item
                    for item in edge.evidence
                    if not (
                        item.resolution_kind is ResolutionKind.EXACT_BASE
                        and (edge.source, item.fact_id) in replaced
                    )
                )
            )
            if evidence:
                selected.append(replace(edge, evidence=evidence))
        return tuple(selected)
