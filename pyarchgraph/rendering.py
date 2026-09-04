"""Deterministic JSON and Mermaid renderers for analysis results."""

from __future__ import annotations

from enum import Enum
import json
from typing import Any

from pyarchgraph.model import AnalysisResult, Diagnostic, ImportFact


MERMAID_NODE_WARNING_THRESHOLD = 200


def _value(value: Enum | str) -> str:
    return value.value if isinstance(value, Enum) else value


def _fact_sort_key(fact: ImportFact) -> tuple[Any, ...]:
    return (
        fact.source,
        fact.path,
        fact.line,
        fact.column,
        fact.end_line if fact.end_line is not None else -1,
        fact.end_column if fact.end_column is not None else -1,
        fact.alias_index,
        _value(fact.syntax),
        fact.base_module or "",
        fact.imported_name or "",
        fact.as_name or "",
        fact.bound_name,
        fact.relative_level,
        _value(fact.scope),
        fact.type_only,
        fact.source_segment or "",
    )


def _diagnostic_sort_key(diagnostic: Diagnostic) -> tuple[Any, ...]:
    return (
        _value(diagnostic.severity),
        diagnostic.code,
        diagnostic.path or "",
        diagnostic.line if diagnostic.line is not None else -1,
        diagnostic.column if diagnostic.column is not None else -1,
        diagnostic.message,
    )


def _diagnostic_json(diagnostic: Diagnostic) -> dict[str, Any]:
    rendered: dict[str, Any] = {
        "severity": _value(diagnostic.severity),
        "code": diagnostic.code,
        "message": diagnostic.message,
    }
    if diagnostic.path is not None:
        rendered["path"] = diagnostic.path
    if diagnostic.line is not None:
        rendered["line"] = diagnostic.line
    if diagnostic.column is not None:
        rendered["column"] = diagnostic.column
    return rendered


def _as_json_model(result: AnalysisResult) -> dict[str, Any]:
    """Convert domain objects to the complete v0.1 JSON boundary model."""

    modules = sorted(result.modules, key=lambda module: module.id)
    facts = sorted(result.import_facts, key=_fact_sort_key)
    dependencies = sorted(
        result.dependencies,
        key=lambda dependency: (dependency.source, dependency.target),
    )
    external_imports = sorted(
        result.external_imports,
        key=lambda external: (
            external.source,
            external.requested,
            _value(external.classification),
        ),
    )
    unresolved_imports = sorted(
        result.unresolved_imports,
        key=lambda unresolved: (
            unresolved.source,
            unresolved.requested,
            _value(unresolved.reason),
        ),
    )
    dag_nodes = sorted(
        result.dag.nodes,
        key=lambda node: (tuple(sorted(node.members)), node.id),
    )
    dag_edges = sorted(result.dag.edges, key=lambda edge: (edge.source, edge.target))

    return {
        "schema_version": "0.1",
        "semantics": {
            "node_kind": "python_module",
            "edge_kind": "syntactic_import",
            "edge_direction": "importer_to_imported",
            "implicit_parent_package_imports": False,
            "namespace_packages": "prefixes_known_nodes_not_emitted",
            "dynamic_imports": "diagnosed_not_resolved",
        },
        "analysis": {
            "complete": result.complete,
            "python_version": result.python_version,
            "namespace_prefixes": sorted(result.namespace_prefixes),
            "view": {"kind": "module"},
        },
        "modules": [
            {
                "id": module.id,
                "path": module.path,
                "is_package": module.is_package,
                "parent_package": module.parent_package,
            }
            for module in modules
        ],
        "import_facts": [
            {
                "id": fact.id,
                "source": fact.source,
                "path": fact.path,
                "line": fact.line,
                "column": fact.column,
                "end_line": fact.end_line,
                "end_column": fact.end_column,
                "alias_index": fact.alias_index,
                "syntax": _value(fact.syntax),
                "source_segment": fact.source_segment,
                "base_module": fact.base_module,
                "imported_name": fact.imported_name,
                "as_name": fact.as_name,
                "bound_name": fact.bound_name,
                "relative_level": fact.relative_level,
                "scope": _value(fact.scope),
                "type_only": fact.type_only,
            }
            for fact in facts
        ],
        "dependencies": [
            {
                "source": dependency.source,
                "target": dependency.target,
                "evidence": [
                    {
                        "fact_id": evidence.fact_id,
                        "resolution_kind": _value(evidence.resolution_kind),
                    }
                    for evidence in sorted(
                        dependency.evidence,
                        key=lambda item: (
                            item.fact_id,
                            _value(item.resolution_kind),
                        ),
                    )
                ],
            }
            for dependency in dependencies
        ],
        "external_imports": [
            {
                "source": external.source,
                "requested": external.requested,
                "classification": _value(external.classification),
                "fact_ids": sorted(external.fact_ids),
            }
            for external in external_imports
        ],
        "unresolved_imports": [
            {
                "source": unresolved.source,
                "requested": unresolved.requested,
                "reason": _value(unresolved.reason),
                "fact_ids": sorted(unresolved.fact_ids),
            }
            for unresolved in unresolved_imports
        ],
        "dag": {
            "nodes": [
                {
                    "id": node.id,
                    "members": sorted(node.members),
                    "cyclic": node.cyclic,
                }
                for node in dag_nodes
            ],
            "edges": [
                {
                    "source": edge.source,
                    "target": edge.target,
                    "raw_dependencies": [
                        {"source": raw.source, "target": raw.target}
                        for raw in sorted(
                            edge.raw_dependencies,
                            key=lambda item: (item.source, item.target),
                        )
                    ],
                }
                for edge in dag_edges
            ],
            "dependency_first_layers": [
                sorted(layer) for layer in result.dag.dependency_first_layers
            ],
        },
        "diagnostics": [
            _diagnostic_json(diagnostic)
            for diagnostic in sorted(result.diagnostics, key=_diagnostic_sort_key)
        ],
    }


def render_json(result: AnalysisResult) -> str:
    """Render the complete canonical v0.1 JSON document."""

    return (
        json.dumps(
            _as_json_model(result),
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    )


def _escape_mermaid_label(label: str) -> str:
    """Escape text embedded in a Mermaid double-quoted node label."""

    return (
        label.replace("&", "&amp;")
        .replace("\\", "&#92;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\r", "&#13;")
        .replace("\n", "&#10;")
        .replace("\t", "&#9;")
    )


def render_mermaid_markdown(result: AnalysisResult) -> str:
    """Render Markdown using only the already-derived condensation DAG."""

    nodes = sorted(
        result.dag.nodes,
        key=lambda node: (tuple(sorted(node.members)), node.id),
    )
    node_ids = {node.id: f"n{index:04d}" for index, node in enumerate(nodes, 1)}

    lines = [
        "# Python dependency DAG",
        "",
        "Generated file.",
        "",
        (
            "Legend: `A -> B` means A contains an import statically resolved "
            "to B. Cycle nodes are strongly connected components."
        ),
        "",
    ]
    if len(nodes) > MERMAID_NODE_WARNING_THRESHOLD:
        lines.extend(
            [
                (
                    f"> Warning: this DAG has {len(nodes)} nodes, above the "
                    f"advisory Mermaid threshold of "
                    f"{MERMAID_NODE_WARNING_THRESHOLD}. The full graph is "
                    "included; consider a future package-prefix projection if "
                    "module-level rendering is unreadable."
                ),
                "",
            ]
        )

    lines.extend(["```mermaid", "flowchart LR"])
    for node in nodes:
        members = tuple(sorted(node.members))
        if node.cyclic:
            label = f"Cycle ({len(members)}): {', '.join(members)}"
            class_suffix = ":::cycle"
        else:
            label = members[0] if len(members) == 1 else ", ".join(members)
            class_suffix = ""
        lines.append(
            f'    {node_ids[node.id]}["{_escape_mermaid_label(label)}"]{class_suffix}'
        )

    for edge in sorted(result.dag.edges, key=lambda item: (item.source, item.target)):
        source_id = node_ids[edge.source]
        target_id = node_ids[edge.target]
        raw_count = len(edge.raw_dependencies)
        if raw_count > 1:
            lines.append(f"    {source_id} -->|{raw_count} imports| {target_id}")
        else:
            lines.append(f"    {source_id} --> {target_id}")

    lines.extend(
        [
            "    classDef cycle fill:#fff1f2,stroke:#be123c,stroke-width:2px",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"
