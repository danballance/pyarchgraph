"""Deterministic JSON and Mermaid renderers for analysis results."""

from __future__ import annotations

from enum import Enum
import json
from typing import Any

from pyarchgraph.graph_ops import essential_edges
from pyarchgraph.model import AnalysisResult, Dag, Diagnostic, ImportFact, View


MERMAID_NODE_WARNING_THRESHOLD = 200


class ImpliedEdges(str, Enum):
    """How to draw an edge that a longer path already implies.

    The transitive reduction is reachability-preserving but weight-blind: it
    drops an edge whenever some other path reaches the same target, however
    much of the codebase's coupling that edge carries. On a layered system the
    heaviest edges are exactly the ones with an alternative path — a
    foundation package is reached both directly and through every layer above
    it — so omitting them draws a graph sparser than the code is.

    ``DOTTED`` therefore draws every edge and distinguishes the implied ones,
    keeping the essential skeleton legible without understating coupling.
    ``OMIT`` is the reduction proper, for when only the shape matters.
    """

    DOTTED = "dotted"
    SOLID = "solid"
    OMIT = "omit"


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


def _view_json(result: AnalysisResult) -> dict[str, Any]:
    """Describe the grain of the emitted DAG, including its projection depth."""

    rendered: dict[str, Any] = {"kind": _value(result.view)}
    if result.package_depth is not None:
        rendered["package_depth"] = result.package_depth
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
            "excludes": sorted(result.excludes),
            "namespace_prefixes": sorted(result.namespace_prefixes),
            "view": _view_json(result),
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


def _layer_index(dag: Dag) -> dict[str, int]:
    """Map each node ID to its dependency-first layer position."""

    return {
        node_id: index
        for index, layer in enumerate(dag.dependency_first_layers)
        for node_id in layer
    }


def _node_label(members: tuple[str, ...], cyclic: bool) -> tuple[str, str]:
    """Return the Mermaid label and class suffix for one condensed node."""

    if cyclic:
        return f"Cycle ({len(members)}): {', '.join(members)}", ":::cycle"
    return (members[0] if len(members) == 1 else ", ".join(members)), ""


def _mermaid_edge_line(
    source_id: str,
    target_id: str,
    raw_count: int,
    *,
    dotted: bool,
) -> str:
    """Render one edge, labelled with the imports behind it when it stands for
    more than one.

    Mermaid accepts both ``a -. text .-> b`` and ``a -.->|text| b`` for a
    labelled dotted link. The pipe form is used here so that the label is
    delimited the same way as on a solid edge, and so a label containing a
    period cannot be mistaken for the link's own punctuation.
    """

    arrow = "-.->" if dotted else "-->"
    if raw_count > 1:
        return f"    {source_id} {arrow}|{raw_count} imports| {target_id}"
    return f"    {source_id} {arrow} {target_id}"


def render_mermaid_markdown(
    result: AnalysisResult,
    *,
    implied_edges: ImpliedEdges = ImpliedEdges.DOTTED,
) -> str:
    """Render Markdown using only the already-derived condensation DAG.

    Nodes are grouped into ``subgraph`` blocks by dependency-first layer and
    drawn top-down, so the drawing carries the layering the analysis already
    computed instead of leaving it to the layout engine to rediscover.

    ``implied_edges`` controls edges a longer path already implies: drawn
    dotted by default, so the diagram is complete and its skeleton is still
    legible. See :class:`ImpliedEdges` for why omitting them by default
    understates coupling.
    """

    dag = result.dag
    nodes = sorted(
        dag.nodes,
        key=lambda node: (tuple(sorted(node.members)), node.id),
    )
    node_ids = {node.id: f"n{index:04d}" for index, node in enumerate(nodes, 1)}
    layer_of = _layer_index(dag)

    essential = essential_edges(dag)
    implied = [
        edge for edge in dag.edges if (edge.source, edge.target) not in essential
    ]

    grain = "package" if result.view is View.PACKAGE else "module"
    legend = (
        "Legend: `A -> B` means A contains an import statically resolved "
        f"to B. Each node is one {grain}; a node with several members is a "
        "strongly connected component. Subgraphs are dependency-first "
        "layers, so an edge always points down the page."
    )
    if implied and implied_edges is ImpliedEdges.DOTTED:
        legend += (
            " A dotted edge is one a longer path already implies; it is a real "
            "import all the same, and often a heavy one."
        )
    lines = [
        "# Python dependency DAG",
        "",
        "Generated file.",
        "",
        legend,
        "",
    ]
    if implied:
        note = {
            ImpliedEdges.DOTTED: (
                f"> {len(implied)} of {len(dag.edges)} edges are implied by a "
                "longer path and are drawn dotted."
            ),
            ImpliedEdges.SOLID: (
                f"> {len(implied)} of {len(dag.edges)} edges are implied by a "
                "longer path. All edges are drawn alike."
            ),
            ImpliedEdges.OMIT: (
                f"> {len(implied)} of {len(dag.edges)} edges are implied by a "
                "longer path and are not drawn. Reachability is unchanged, but "
                "the omitted edges may carry most of the coupling; "
                "`dependency-graph.json` lists every edge."
            ),
        }[implied_edges]
        lines.extend([note, ""])
    if len(nodes) > MERMAID_NODE_WARNING_THRESHOLD:
        lines.extend(
            [
                (
                    f"> Warning: this DAG has {len(nodes)} nodes, above the "
                    f"advisory Mermaid threshold of "
                    f"{MERMAID_NODE_WARNING_THRESHOLD}. Consider "
                    "`--view package` for a readable projection."
                ),
                "",
            ]
        )

    lines.extend(["```mermaid", "flowchart TD"])

    # Most-dependent layer first, so the source order matches the drawn order.
    for index in range(len(dag.dependency_first_layers) - 1, -1, -1):
        members = [node for node in nodes if layer_of.get(node.id) == index]
        if not members:
            continue
        lines.append(f'    subgraph layer{index}["Layer {index}"]')
        for node in members:
            label, class_suffix = _node_label(tuple(sorted(node.members)), node.cyclic)
            lines.append(
                f"        {node_ids[node.id]}"
                f'["{_escape_mermaid_label(label)}"]{class_suffix}'
            )
        lines.append("    end")

    for node in nodes:
        if node.id not in layer_of:
            label, class_suffix = _node_label(tuple(sorted(node.members)), node.cyclic)
            lines.append(
                f"    {node_ids[node.id]}"
                f'["{_escape_mermaid_label(label)}"]{class_suffix}'
            )

    for edge in sorted(dag.edges, key=lambda item: (item.source, item.target)):
        is_implied = (edge.source, edge.target) not in essential
        if is_implied and implied_edges is ImpliedEdges.OMIT:
            continue
        lines.append(
            _mermaid_edge_line(
                node_ids[edge.source],
                node_ids[edge.target],
                len(edge.raw_dependencies),
                dotted=is_implied and implied_edges is ImpliedEdges.DOTTED,
            )
        )

    lines.extend(
        [
            "    classDef cycle fill:#fff1f2,stroke:#be123c,stroke-width:2px",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"
