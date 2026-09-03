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
)
from pyarchgraph.resolution import resolve_imports


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
    fact_source: ImportFactSource | None = None,
) -> AnalysisResult:
    """Analyse one explicit import root without importing any target code.

    The result is complete if and only if every candidate file received an
    unambiguous module identity and was successfully read, decoded and parsed.
    In concrete terms, this is exactly when no error diagnostic was produced.
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
    dag = build_dag(discovery.modules, resolution.dependencies)
    diagnostics = tuple(
        sorted((*discovery.diagnostics, *collection.diagnostics), key=_diagnostic_key)
    )

    return AnalysisResult(
        complete=not any(item.severity is Severity.ERROR for item in diagnostics),
        python_version=(
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ),
        namespace_prefixes=discovery.namespace_prefixes,
        modules=discovery.modules,
        import_facts=facts,
        dependencies=resolution.dependencies,
        external_imports=resolution.external_imports,
        unresolved_imports=resolution.unresolved_imports,
        dag=dag,
        diagnostics=diagnostics,
    )
