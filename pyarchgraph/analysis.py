"""Orchestrate the facts -> graph -> evidence analysis pipeline."""

from __future__ import annotations

import sys
from pathlib import Path

from pyarchgraph.discovery import discover_modules
from pyarchgraph.extraction import AstImportFactSource, canonicalise_fact_ids
from pyarchgraph.graph_ops import build_dag
from pyarchgraph.model import (
    AnalysisResult,
    Diagnostic,
    ImportFactSource,
    Severity,
    View,
)
from pyarchgraph.projection import build_package_dag
from pyarchgraph.quality import calculate_quality
from pyarchgraph.resolution import resolve_imports

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

    discovery = discover_modules(root, excludes=excludes)
    collector = fact_source if fact_source is not None else AstImportFactSource()
    collection = collector.collect(root, discovery.modules)
    facts = canonicalise_fact_ids(collection.facts)
    resolution = resolve_imports(
        facts,
        discovery.modules,
        discovery.namespace_prefixes,
    )
    if view is View.PACKAGE:
        dag = build_package_dag(
            discovery.modules, resolution.dependencies, package_depth
        )
    else:
        dag = build_dag(discovery.modules, resolution.dependencies)
    diagnostics = tuple(
        sorted((*discovery.diagnostics, *collection.diagnostics), key=_diagnostic_key)
    )
    complete = not any(item.severity is Severity.ERROR for item in diagnostics)
    quality = calculate_quality(
        discovery.modules,
        resolution.dependencies,
        complete=complete,
        unresolved_import_count=len(resolution.unresolved_imports),
        dynamic_import_warning_count=sum(
            item.severity is Severity.WARNING and item.code == "dynamic_import_ignored"
            for item in diagnostics
        ),
    )

    return AnalysisResult(
        complete=complete,
        python_version=(
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ),
        excludes=tuple(excludes),
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
    )
