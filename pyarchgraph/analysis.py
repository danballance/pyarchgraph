"""Orchestrate the facts -> graph -> evidence analysis pipeline."""

from __future__ import annotations

from dataclasses import replace
import sys
from pathlib import Path

from pyarchgraph.discovery import discover_modules
from pyarchgraph.extraction import AstImportFactSource, canonicalise_fact_ids
from pyarchgraph.graph_ops import build_dag
from pyarchgraph.findings import build_findings
from pyarchgraph.model import (
    AnalysisResult,
    Diagnostic,
    ImportFactSource,
    Severity,
    UnresolvedReason,
    View,
)
from pyarchgraph.projection import build_package_dag
from pyarchgraph.policy import (
    DEFINITE_KINDS,
    GraphPolicy,
    TEST_EXCLUDES,
    filter_dependencies,
)
from pyarchgraph.provenance import build_provenance
from pyarchgraph.quality import calculate_quality
from pyarchgraph.resolution import architecture_dependencies, resolve_imports

DEFAULT_PACKAGE_DEPTH = 2


def _diagnostic_key(
    diagnostic: Diagnostic,
) -> tuple[str, str, str, int, int, str]:
    return (
        diagnostic.severity.value,
        diagnostic.code,
        diagnostic.path or "",
        diagnostic.line if diagnostic.line is not None else -1,
        diagnostic.column if diagnostic.column is not None else -1,
        diagnostic.message,
    )


def analyse(
    source_root: Path,
    *,
    excludes: tuple[str, ...] = (),
    view: View = View.MODULE,
    package_depth: int = DEFAULT_PACKAGE_DEPTH,
    fact_source: ImportFactSource | None = None,
    project_root: Path | None = None,
    expected_packages: tuple[str, ...] = (),
    policy: GraphPolicy = GraphPolicy(),
    forbidden_dependencies: tuple[tuple[str, str], ...] = (),
) -> AnalysisResult:
    """Analyse one explicit import root without importing any target code.

    The result is complete if and only if every candidate file received an
    unambiguous module identity and was successfully read, decoded and parsed.
    In concrete terms, this is exactly when no error diagnostic was produced.

    ``view`` selects the grain of the condensation DAG only. Modules, import
    facts and module-level dependencies are always reported at module grain,
    so a package view narrows what is drawn and never what is recorded.
    """

    root = Path(source_root)
    if not root.is_dir():
        raise ValueError("source_root must be an existing directory")

    project = Path(project_root) if project_root is not None else root
    try:
        relative_root = root.resolve().relative_to(project.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError("source_root must be inside project_root") from exc
    if any(
        not name or any(not part.isidentifier() for part in name.split("."))
        for name in expected_packages
    ):
        raise ValueError("expected package names must be dotted Python identifiers")
    if any(len(rule) != 2 or not all(rule) for rule in forbidden_dependencies):
        raise ValueError(
            "forbidden dependencies require nonempty source and target patterns"
        )
    forbidden_dependencies = tuple(sorted(set(forbidden_dependencies)))
    effective_excludes = tuple(
        sorted(set(excludes) | (set() if policy.include_tests else set(TEST_EXCLUDES)))
    )

    discovery = discover_modules(root, excludes=effective_excludes)
    collector = fact_source if fact_source is not None else AstImportFactSource()
    collection = (
        collector.collect(root, discovery.modules)
        if fact_source is not None
        else collector.collect_uncanonicalised(root, discovery.modules)
    )
    facts = canonicalise_fact_ids(collection.facts)
    resolution = resolve_imports(
        facts,
        discovery.modules,
        discovery.namespace_prefixes,
    )
    structural = filter_dependencies(
        architecture_dependencies(resolution.dependencies), facts, policy
    )
    if view is View.PACKAGE:
        dag = build_package_dag(discovery.modules, structural, package_depth)
    else:
        dag = build_dag(discovery.modules, structural)
    scope_diagnostics = []
    known_names = {module.id for module in discovery.modules} | set(
        discovery.namespace_prefixes
    )
    for name in sorted(set(expected_packages) - known_names):
        scope_diagnostics.append(
            Diagnostic(
                Severity.WARNING,
                "expected_package_missing",
                f"Expected package {name!r} is absent; verify source root and exclusions.",
            )
        )
    # Detect the common src-layout mistake without treating every unknown
    # external import as missing internal code. This is deliberately a warning.
    suspicious = sorted(
        {
            item.requested.split(".")[0]
            for item in resolution.external_imports
            if f"src.{item.requested.split('.')[0]}" in known_names
        }
    )
    if suspicious:
        scope_diagnostics.append(
            Diagnostic(
                Severity.WARNING,
                "source_root_mismatch",
                "Imports match packages below src/: "
                + ", ".join(suspicious)
                + "; use src as the source root and record its project root.",
            )
        )
    diagnostics = tuple(
        sorted(
            (*discovery.diagnostics, *collection.diagnostics, *scope_diagnostics),
            key=_diagnostic_key,
        )
    )
    complete = not any(item.severity is Severity.ERROR for item in diagnostics)
    scope_valid = not scope_diagnostics
    dynamic_count = sum(item.code == "dynamic_import_ignored" for item in diagnostics)
    quality = calculate_quality(
        discovery.modules,
        structural,
        complete=complete,
        unresolved_import_count=len(resolution.unresolved_imports),
        dynamic_import_warning_count=dynamic_count,
    )
    if complete and not scope_valid:
        quality = replace(
            quality,
            score=None,
            cycle_avoidance_score=None,
            dependency_isolation_score=None,
            unavailable_reason="invalid_scope",
        )
    missing = any(
        item.reason is not UnresolvedReason.NAMESPACE_BASE_UNMODELLED
        for item in resolution.unresolved_imports
    )
    uncertain = any(
        not any(item.resolution_kind in DEFINITE_KINDS for item in edge.evidence)
        for edge in structural
    )
    limitations = [
        "Static analysis does not prove runtime initialization or the absence of dynamic imports.",
        "The heuristic score is not a policy threshold; connected additions can dilute it.",
    ]
    if not expected_packages:
        limitations.append(
            "No expected package names were supplied; source-root validation is heuristic."
        )
    if missing:
        limitations.append(
            "Internal targets or relative imports could not be resolved."
        )
    if uncertain:
        limitations.append(
            "Some relationships depend on probable submodules or literal dynamic targets."
        )
    if dynamic_count:
        limitations.append(
            "Recognized dynamic calls are warnings; only supported literal targets are modelled."
        )
    python_version = (
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )
    provenance = build_provenance(
        relative_root,
        python_version,
        effective_excludes,
        expected_packages,
        policy,
        forbidden_dependencies,
        fact_source_name=f"{type(collector).__module__}.{type(collector).__qualname__}",
    )

    return AnalysisResult(
        complete=complete,
        python_version=python_version,
        excludes=effective_excludes,
        view=view,
        package_depth=package_depth if view is View.PACKAGE else None,
        namespace_prefixes=discovery.namespace_prefixes,
        modules=discovery.modules,
        import_facts=facts,
        dependencies=resolution.dependencies,
        external_imports=resolution.external_imports,
        unresolved_imports=resolution.unresolved_imports,
        dag=dag,
        diagnostics=diagnostics,
        quality=quality,
        architecture_dependencies=structural,
        provenance=provenance,
        findings=build_findings(structural, facts, forbidden_dependencies),
        limitations=tuple(limitations),
        scope_valid=scope_valid,
        dependency_resolution_complete=(
            complete and scope_valid and not (missing or dynamic_count or uncertain)
        ),
    )
