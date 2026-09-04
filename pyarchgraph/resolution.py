"""Resolve import facts strictly against the discovered source inventory."""

from __future__ import annotations

import importlib.util
import sys

from pyarchgraph.model import (
    DependencyEdge,
    DependencyEvidence,
    ExternalClassification,
    ExternalImport,
    ImportFact,
    ImportSyntax,
    ResolutionKind,
    ResolutionResult,
    SourceModule,
    UnresolvedImport,
    UnresolvedReason,
)


def resolve_imports(
    facts: tuple[ImportFact, ...],
    modules: tuple[SourceModule, ...],
    namespace_prefixes: tuple[str, ...],
) -> ResolutionResult:
    """Resolve facts without consulting ``sys.path`` or importing any target.

    An internal edge points from the source module to the source-backed module
    it names.  Absent names are classified using only the inventory,
    namespace prefixes, and ``sys.stdlib_module_names``.
    """

    module_by_id = {module.id: module for module in modules}
    module_ids = frozenset(module_by_id)
    namespace_ids = frozenset(namespace_prefixes)
    internal_top_levels = frozenset(
        name.partition(".")[0] for name in (*module_ids, *namespace_ids) if name
    )

    dependency_evidence: dict[tuple[str, str], list[DependencyEvidence]] = {}
    external_fact_ids: dict[tuple[str, str, ExternalClassification], list[str]] = {}
    unresolved_fact_ids: dict[tuple[str, str, UnresolvedReason], list[str]] = {}

    def add_dependency(fact: ImportFact, target: str, kind: ResolutionKind) -> None:
        dependency_evidence.setdefault((fact.source, target), []).append(
            DependencyEvidence(fact_id=fact.id, resolution_kind=kind)
        )

    def add_external(
        fact: ImportFact,
        requested: str,
        classification: ExternalClassification,
    ) -> None:
        external_fact_ids.setdefault(
            (fact.source, requested, classification), []
        ).append(fact.id)

    def add_unresolved(
        fact: ImportFact,
        requested: str,
        reason: UnresolvedReason,
    ) -> None:
        unresolved_fact_ids.setdefault((fact.source, requested, reason), []).append(
            fact.id
        )

    def classify_absent(fact: ImportFact, requested: str) -> None:
        """Apply the plan's inventory, namespace, stdlib precedence."""

        if requested in namespace_ids:
            add_unresolved(
                fact,
                requested,
                UnresolvedReason.NAMESPACE_BASE_UNMODELLED,
            )
            return

        top_level = requested.partition(".")[0]
        if top_level in internal_top_levels:
            add_unresolved(
                fact,
                requested,
                UnresolvedReason.MISSING_INTERNAL_TARGET,
            )
        elif top_level in sys.stdlib_module_names:
            add_external(fact, requested, ExternalClassification.STDLIB)
        else:
            add_external(
                fact,
                requested,
                ExternalClassification.EXTERNAL_UNKNOWN,
            )

    for fact in facts:
        if fact.syntax == ImportSyntax.IMPORT:
            # ``base_module`` is the canonical extraction representation.  The
            # fallback also makes the resolver tolerant of protocol-backed fact
            # sources that retain an Import alias in ``imported_name``.
            requested = fact.base_module or fact.imported_name or ""
            if requested in module_ids:
                add_dependency(fact, requested, ResolutionKind.EXACT_MODULE)
            else:
                classify_absent(fact, requested)
            continue

        raw_base = fact.base_module or ""
        if fact.relative_level:
            relative_requested = "." * fact.relative_level + raw_base
            source_module = module_by_id.get(fact.source)
            if source_module is None:
                add_unresolved(
                    fact,
                    relative_requested,
                    UnresolvedReason.RELATIVE_ESCAPE,
                )
                continue

            current_package = (
                source_module.id
                if source_module.is_package
                else source_module.parent_package
            )
            if not current_package:
                add_unresolved(
                    fact,
                    relative_requested,
                    UnresolvedReason.RELATIVE_ESCAPE,
                )
                continue

            try:
                absolute_base = importlib.util.resolve_name(
                    relative_requested, current_package
                )
            except ImportError:
                add_unresolved(
                    fact,
                    relative_requested,
                    UnresolvedReason.RELATIVE_ESCAPE,
                )
                continue
        else:
            absolute_base = raw_base

        if absolute_base in module_ids:
            # ``from current_package import ...`` is common in package
            # ``__init__.py`` files.  Its definite base relation is a trivial
            # self-edge, but a source-backed submodule candidate is retained.
            if fact.source != absolute_base:
                add_dependency(fact, absolute_base, ResolutionKind.EXACT_BASE)
        else:
            classify_absent(fact, absolute_base)

        imported_name = fact.imported_name
        if imported_name and imported_name != "*":
            candidate = f"{absolute_base}.{imported_name}"
            if candidate in module_ids:
                add_dependency(
                    fact,
                    candidate,
                    ResolutionKind.PROBABLE_SUBMODULE,
                )

    dependencies = tuple(
        DependencyEdge(
            source=source,
            target=target,
            evidence=tuple(
                sorted(
                    evidence,
                    key=lambda item: (item.fact_id, item.resolution_kind.value),
                )
            ),
        )
        for (source, target), evidence in sorted(dependency_evidence.items())
    )
    external_imports = tuple(
        ExternalImport(
            source=source,
            requested=requested,
            classification=classification,
            fact_ids=tuple(sorted(fact_ids)),
        )
        for (source, requested, classification), fact_ids in sorted(
            external_fact_ids.items(),
            key=lambda item: (item[0][0], item[0][1], item[0][2].value),
        )
    )
    unresolved_imports = tuple(
        UnresolvedImport(
            source=source,
            requested=requested,
            reason=reason,
            fact_ids=tuple(sorted(fact_ids)),
        )
        for (source, requested, reason), fact_ids in sorted(
            unresolved_fact_ids.items(),
            key=lambda item: (item[0][0], item[0][1], item[0][2].value),
        )
    )

    return ResolutionResult(
        dependencies=dependencies,
        external_imports=external_imports,
        unresolved_imports=unresolved_imports,
    )


__all__ = ["resolve_imports"]
