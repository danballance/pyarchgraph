"""Resolve explicit imports against declared source identities and boundaries."""

from __future__ import annotations

import importlib.util
import sys
from collections import defaultdict
from dataclasses import replace
from pathlib import PurePosixPath

from pyarchgraph.model import (
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


def _within(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def _prefixes(name: str) -> tuple[str, ...]:
    parts = name.split(".")
    return tuple(".".join(parts[:index]) for index in range(1, len(parts) + 1))


def _native_artifact(target: TargetDeclaration) -> bool:
    return target.kind == "native" and bool(
        target.path and target.path.endswith((".so", ".pyd"))
    )


def _location(fact: ImportFact) -> EvidenceLocation:
    return EvidenceLocation(
        fact.path, fact.line, fact.column + 1, fact.source_segment, None, fact.context
    )


def resolve_imports(
    facts: tuple[ImportFact, ...],
    modules: tuple[SourceModule, ...],
    namespace_prefixes: tuple[str, ...],
    *,
    owned_prefixes: tuple[str, ...] = (),
    targets: tuple[TargetDeclaration, ...] = (),
) -> ResolutionResult:
    """Resolve without consulting installed packages or executing target code.

    Source IDs are opaque graph identities; import names form a separate
    binding index. Pure namespace ancestors do not imply ownership of absent
    siblings. Explicit ownership prefixes extend inferred regular-package and
    exact-module ownership. Non-Python targets are coverage boundaries, not
    invented source nodes or missing-source findings.
    """
    by_id = {module.id: module for module in modules}
    names: defaultdict[str, list[SourceModule]] = defaultdict(list)
    for module in modules:
        if module.import_name and module.binding_status in {"bound", "ambiguous"}:
            names[module.import_name].append(module)
    ambiguous = {
        name
        for name, candidates in names.items()
        if len(candidates) != 1
        or any(item.binding_status == "ambiguous" for item in candidates)
    }
    bound = {
        name: candidates[0]
        for name, candidates in names.items()
        if name not in ambiguous
    }
    owned = frozenset((*owned_prefixes, *names))
    namespaces = frozenset(namespace_prefixes) - frozenset(names)
    target_groups: defaultdict[str, list[TargetDeclaration]] = defaultdict(list)
    for target in targets:
        target_groups[target.name].append(target)

    # Multiple artifacts in one directory can be source/build/stub companions.
    # Distinct native implementations or a native artifact competing with .py
    # cannot be resolved by an acknowledgement of incomplete coverage.
    native_ambiguities = []
    for name, declarations in sorted(target_groups.items()):
        native = [item for item in declarations if item.kind == "native"]
        native_locations = {
            str(PurePosixPath(item.path).parent) for item in native if item.path
        }
        if len(native_locations) > 1 or (
            name in bound and any(_native_artifact(item) for item in native)
        ):
            ambiguous.add(name)
            native_ambiguities.append(
                Diagnostic(
                    Severity.ERROR,
                    "ambiguous_target",
                    f"Import name {name!r} has competing Python/native implementations.",
                    sorted(item.path for item in native if item.path)[0],
                )
            )

    dependency_evidence: defaultdict[tuple[str, str], list[DependencyEvidence]] = (
        defaultdict(list)
    )
    external_ids: defaultdict[tuple[str, str, ExternalClassification], list[str]] = (
        defaultdict(list)
    )
    unresolved_ids: defaultdict[tuple[str, str, UnresolvedReason], list[str]] = (
        defaultdict(list)
    )
    boundary_facts: defaultdict[TargetDeclaration, list[ImportFact]] = defaultdict(list)
    diagnostics: list[Diagnostic] = native_ambiguities

    def unresolved(fact: ImportFact, requested: str, reason: UnresolvedReason) -> None:
        unresolved_ids[(fact.source, requested, reason)].append(fact.id)
        if reason in {
            UnresolvedReason.AMBIGUOUS_TARGET,
            UnresolvedReason.UNKNOWN_PACKAGE_CONTEXT,
        }:
            message = (
                f"Import {requested!r} has more than one possible source or native binding."
                if reason is UnresolvedReason.AMBIGUOUS_TARGET
                else f"Relative import {requested!r} has no established package context for this path-only source."
            )
            diagnostics.append(
                Diagnostic(
                    Severity.ERROR,
                    reason.value,
                    message,
                    fact.path,
                    fact.line,
                    fact.column + 1,
                )
            )

    def target_for(requested: str) -> TargetDeclaration | None:
        declarations = list(target_groups.get(requested, ()))
        if not declarations:
            # Directory exclusions are prefix boundaries, never recursively read.
            candidates = [
                item
                for item in targets
                if item.kind == "excluded"
                and item.path
                and item.path.endswith("/")
                and _within(requested, item.name)
            ]
            if candidates:
                longest = max(len(item.name) for item in candidates)
                declarations = [
                    item for item in candidates if len(item.name) == longest
                ]
        if not declarations:
            return None
        kinds = {item.kind for item in declarations}
        # A stub accompanies native/generated implementations without changing
        # the availability classification. Explicit acknowledgements are merged.
        kind = next(
            item
            for item in ("native", "generated", "stub", "excluded")
            if item in kinds
        )
        selected = sorted(
            (item for item in declarations if item.kind == kind),
            key=lambda item: (item.path is None, item.path or "", item.reason),
        )[0]
        acknowledged = any(
            item.acknowledged for item in declarations if item.kind == kind
        )
        reasons = sorted({item.reason for item in declarations if item.kind == kind})
        return replace(
            selected,
            name=requested,
            acknowledged=acknowledged,
            reason=" ".join(reasons),
        )

    def classify_absent(fact: ImportFact, requested: str) -> None:
        if any(prefix in ambiguous for prefix in _prefixes(requested)):
            unresolved(fact, requested, UnresolvedReason.AMBIGUOUS_TARGET)
            return
        if declaration := target_for(requested):
            boundary_facts[declaration].append(fact)
            return
        if requested in namespaces:
            unresolved(fact, requested, UnresolvedReason.NAMESPACE_BASE_UNMODELLED)
            return
        if any(prefix in owned for prefix in _prefixes(requested)):
            unresolved(fact, requested, UnresolvedReason.MISSING_INTERNAL_TARGET)
        else:
            classification = (
                ExternalClassification.STDLIB
                if requested.partition(".")[0] in sys.stdlib_module_names
                else ExternalClassification.EXTERNAL_UNKNOWN
            )
            external_ids[(fact.source, requested, classification)].append(fact.id)

    def dependency(fact: ImportFact, requested: str, kind: ResolutionKind) -> bool:
        if any(prefix in ambiguous for prefix in _prefixes(requested)):
            unresolved(fact, requested, UnresolvedReason.AMBIGUOUS_TARGET)
            return True
        if module := bound.get(requested):
            dependency_evidence[(fact.source, module.id)].append(
                DependencyEvidence(fact.id, kind)
            )
            return True
        return False

    for fact in facts:
        if fact.syntax is ImportSyntax.IMPORT:
            assert fact.base_module is not None
            if not dependency(fact, fact.base_module, ResolutionKind.EXACT_MODULE):
                classify_absent(fact, fact.base_module)
            continue

        absolute_base = fact.base_module or ""
        if fact.relative_level:
            requested = "." * fact.relative_level + absolute_base
            source = by_id.get(fact.source)
            if (
                source is None
                or source.binding_status == "path_only"
                or source.import_name is None
            ):
                unresolved(fact, requested, UnresolvedReason.UNKNOWN_PACKAGE_CONTEXT)
                continue
            package = source.import_name if source.is_package else source.parent_package
            if not package:
                unresolved(fact, requested, UnresolvedReason.RELATIVE_ESCAPE)
                continue
            try:
                absolute_base = importlib.util.resolve_name(requested, package)
            except ImportError:
                unresolved(fact, requested, UnresolvedReason.RELATIVE_ESCAPE)
                continue

        module = bound.get(absolute_base)
        # Suppress only the initializer's trivial own-package base relation.
        # Ordinary-module from-self imports must retain their self dependency.
        suppress_initializer = (
            module is not None and module.is_package and fact.source == module.id
        )
        if not suppress_initializer and not dependency(
            fact, absolute_base, ResolutionKind.EXACT_BASE
        ):
            classify_absent(fact, absolute_base)

        imported = fact.imported_name
        if imported and imported != "*":
            candidate = f"{absolute_base}.{imported}"
            if candidate in names:
                dependency(fact, candidate, ResolutionKind.PROBABLE_SUBMODULE)
            elif target_for(candidate) is not None or absolute_base in namespaces:
                classify_absent(fact, candidate)

    dependencies = tuple(
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
        for (source, target), evidence in sorted(dependency_evidence.items())
    )
    external_imports = tuple(
        ExternalImport(source, requested, classification, tuple(sorted(set(ids))))
        for (source, requested, classification), ids in sorted(
            external_ids.items(),
            key=lambda item: (item[0][0], item[0][1], item[0][2].value),
        )
    )
    unresolved_imports = tuple(
        UnresolvedImport(source, requested, reason, tuple(sorted(set(ids))))
        for (source, requested, reason), ids in sorted(
            unresolved_ids.items(),
            key=lambda item: (item[0][0], item[0][1], item[0][2].value),
        )
    )
    boundaries = tuple(
        TargetBoundary(
            declaration.name,
            declaration.kind,
            declaration.path,
            declaration.reason,
            declaration.acknowledged,
            tuple(
                sorted(
                    {_location(fact) for fact in evidence},
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
            boundary_facts.items(),
            key=lambda item: (item[0].name, item[0].kind, item[0].path or ""),
        )
    )
    return ResolutionResult(
        dependencies,
        external_imports,
        unresolved_imports,
        tuple(
            sorted(
                set(diagnostics),
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


def architecture_dependencies(
    dependencies: tuple[DependencyEdge, ...],
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
            item
            for item in edge.evidence
            if not (
                item.resolution_kind is ResolutionKind.EXACT_BASE
                and (edge.source, item.fact_id) in replaced
            )
        )
        if evidence:
            selected.append(replace(edge, evidence=evidence))
    return tuple(selected)


__all__ = ["architecture_dependencies", "resolve_imports"]
