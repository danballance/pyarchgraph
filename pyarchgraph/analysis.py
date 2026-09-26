"""Discover, resolve, and check one explicit Python import root."""

import os
from dataclasses import replace
from pathlib import Path

from pyarchgraph.discovery import discover_modules
from pyarchgraph.extraction import AstImportFactSource
from pyarchgraph.findings import build_findings
from pyarchgraph.model import AnalysisReport, Diagnostic
from pyarchgraph.resolution import architecture_dependencies, resolve_imports

TEST_EXCLUDES = ("tests", "test_*.py", "*_test.py")


class AnalysisError(ValueError):
    """The selected sources cannot produce a complete dependency check."""


def _raise_errors(diagnostics: tuple[Diagnostic, ...]) -> None:
    messages = []
    for diagnostic in diagnostics:
        location = diagnostic.path or ""
        if diagnostic.line is not None:
            location += f":{diagnostic.line}"
        if diagnostic.column is not None:
            location += f":{diagnostic.column + 1}"
        prefix = f"{location}: " if location else ""
        messages.append(f"{prefix}[{diagnostic.code}]: {diagnostic.message}")
    if messages:
        raise AnalysisError("\n".join(messages))


def analyse(
    source_root: Path,
    *,
    excludes: tuple[str, ...] = (),
) -> AnalysisReport:
    """Check explicit import statements without executing project code.

    Tests are excluded; local and typing imports always count. Incomplete or
    invalid source inventories raise AnalysisError rather than returning a
    partial report. Evidence paths are relative to the caller's working
    directory, and evidence positions are one-based.
    """

    root = Path(source_root)
    if not root.is_dir():
        raise AnalysisError("source_root must be an existing directory")
    effective_excludes = tuple(sorted(set(excludes) | set(TEST_EXCLUDES)))
    discovery = discover_modules(root, excludes=effective_excludes)

    # Include the import-root prefix in evidence for src-layout consumers.
    prefix = Path(os.path.relpath(root.resolve(), Path.cwd().resolve()))

    def located(diagnostic: Diagnostic) -> Diagnostic:
        return replace(
            diagnostic,
            path=(prefix / diagnostic.path).as_posix() if diagnostic.path else None,
        )

    _raise_errors(tuple(located(item) for item in discovery.diagnostics))
    if not discovery.modules:
        raise AnalysisError("[no_modules]: source root contains no Python modules")
    collection = AstImportFactSource().collect(root, discovery.modules)
    diagnostics = tuple(located(item) for item in collection.diagnostics)
    _raise_errors(diagnostics)
    facts = tuple(
        replace(fact, path=(prefix / fact.path).as_posix()) for fact in collection.facts
    )
    modules = tuple(
        replace(module, path=(prefix / module.path).as_posix())
        for module in discovery.modules
    )
    resolution = resolve_imports(facts, modules, discovery.namespace_prefixes)
    known_names = {module.id for module in modules} | set(discovery.namespace_prefixes)
    suspicious = sorted(
        {
            item.requested.split(".")[0]
            for item in resolution.external_imports
            if f"src.{item.requested.split('.')[0]}" in known_names
        }
    )
    if suspicious:
        raise AnalysisError(
            "[source_root_mismatch]: Imports match packages below src/: "
            + ", ".join(suspicious)
            + "; use src as the source root."
        )
    structural = architecture_dependencies(resolution.dependencies)
    return AnalysisReport(
        module_count=len(modules),
        dependency_count=len(structural),
        findings=build_findings(
            structural, facts, resolution.unresolved_imports
        ),
    )
