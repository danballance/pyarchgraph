from __future__ import annotations

import json

from pyarchgraph.model import (
    AnalysisResult,
    Dag,
    DagEdge,
    DagNode,
    DependencyEdge,
    DependencyEvidence,
    Diagnostic,
    ExternalClassification,
    ExternalImport,
    ImportFact,
    ImportScope,
    ImportSyntax,
    RawDependency,
    ResolutionKind,
    Severity,
    SourceModule,
    UnresolvedImport,
    UnresolvedReason,
    View,
)
from pyarchgraph.rendering import (
    ImpliedEdges,
    render_json,
    render_mermaid_markdown,
)
from pyarchgraph.quality import calculate_quality


def _result(*, dag: Dag, **overrides: object) -> AnalysisResult:
    values: dict[str, object] = {
        "complete": True,
        "python_version": "3.14.7",
        "excludes": (),
        "view": View.MODULE,
        "package_depth": None,
        "namespace_prefixes": (),
        "modules": (),
        "import_facts": (),
        "dependencies": (),
        "external_imports": (),
        "unresolved_imports": (),
        "dag": dag,
        "diagnostics": (),
    }
    values.update(overrides)
    values["quality"] = calculate_quality(
        values["modules"] or (),
        values["dependencies"] or (),
        complete=values["complete"],
        unresolved_import_count=len(values["unresolved_imports"] or ()),
        dynamic_import_warning_count=sum(
            diagnostic.code == "dynamic_import_ignored"
            for diagnostic in (values["diagnostics"] or ())
        ),
    )
    return AnalysisResult(**values)  # type: ignore[arg-type]


def test_render_json_emits_complete_contract_and_canonical_order() -> None:
    fact = ImportFact(
        id="fact-z",
        source="a",
        path="a.py",
        line=2,
        column=0,
        end_line=2,
        end_column=8,
        alias_index=0,
        syntax=ImportSyntax.IMPORT,
        source_segment="import b",
        base_module="b",
        imported_name=None,
        as_name=None,
        bound_name="b",
        relative_level=0,
        scope=ImportScope.MODULE,
        type_only=False,
    )
    dag = Dag(
        nodes=(
            DagNode(id="scc-b", members=("b",), cyclic=False),
            DagNode(id="scc-a", members=("a",), cyclic=False),
        ),
        edges=(
            DagEdge(
                source="scc-a",
                target="scc-b",
                raw_dependencies=(RawDependency(source="a", target="b"),),
            ),
        ),
        dependency_first_layers=(("scc-b",), ("scc-a",)),
    )
    result = _result(
        dag=dag,
        namespace_prefixes=("zeta", "alpha"),
        modules=(
            SourceModule("b", "b.py", False, None),
            SourceModule("a", "a.py", False, None),
        ),
        import_facts=(fact,),
        dependencies=(
            DependencyEdge(
                source="a",
                target="b",
                evidence=(DependencyEvidence("fact-z", ResolutionKind.EXACT_MODULE),),
            ),
        ),
        external_imports=(
            ExternalImport("a", "os", ExternalClassification.STDLIB, ("fact-os",)),
        ),
        unresolved_imports=(
            UnresolvedImport(
                "a",
                "a.missing",
                UnresolvedReason.MISSING_INTERNAL_TARGET,
                ("fact-missing",),
            ),
        ),
        diagnostics=(
            Diagnostic(
                Severity.WARNING,
                "dynamic_import_ignored",
                "Dynamic import syntax is outside v0.1.",
            ),
        ),
    )

    rendered = render_json(result)
    payload = json.loads(rendered)

    assert rendered.endswith("\n") and not rendered.endswith("\n\n")
    assert render_json(result) == rendered
    assert payload["schema_version"] == "0.2"
    assert payload["quality"]["score"] == 85.0
    assert payload["quality"]["formula_version"] == "architecture-v1"
    assert payload["quality"]["unresolved_import_count"] == 1
    assert payload["quality"]["dynamic_import_warning_count"] == 1
    assert payload["semantics"] == {
        "dynamic_imports": "diagnosed_not_resolved",
        "edge_direction": "importer_to_imported",
        "edge_kind": "syntactic_import",
        "implicit_parent_package_imports": False,
        "namespace_packages": "prefixes_known_nodes_not_emitted",
        "node_kind": "python_module",
    }
    assert payload["analysis"] == {
        "complete": True,
        "excludes": [],
        "namespace_prefixes": ["alpha", "zeta"],
        "python_version": "3.14.7",
        "view": {"kind": "module"},
    }
    assert [module["id"] for module in payload["modules"]] == ["a", "b"]
    assert payload["import_facts"][0] == {
        "alias_index": 0,
        "as_name": None,
        "base_module": "b",
        "bound_name": "b",
        "column": 0,
        "end_column": 8,
        "end_line": 2,
        "id": "fact-z",
        "imported_name": None,
        "line": 2,
        "path": "a.py",
        "relative_level": 0,
        "scope": "module",
        "source": "a",
        "source_segment": "import b",
        "syntax": "import",
        "type_only": False,
    }
    assert payload["dependencies"][0]["evidence"] == [
        {"fact_id": "fact-z", "resolution_kind": "exact_module"}
    ]
    assert payload["external_imports"] == [
        {
            "classification": "stdlib",
            "fact_ids": ["fact-os"],
            "requested": "os",
            "source": "a",
        }
    ]
    assert payload["unresolved_imports"] == [
        {
            "fact_ids": ["fact-missing"],
            "reason": "missing_internal_target",
            "requested": "a.missing",
            "source": "a",
        }
    ]
    assert [node["members"] for node in payload["dag"]["nodes"]] == [
        ["a"],
        ["b"],
    ]
    assert payload["diagnostics"] == [
        {
            "code": "dynamic_import_ignored",
            "message": "Dynamic import syntax is outside v0.1.",
            "severity": "warning",
        }
    ]


def test_mermaid_is_derived_only_from_dag_and_is_deterministic() -> None:
    dag = Dag(
        nodes=(
            DagNode(id="cycle", members=('b"&<\n', "a\\"), cyclic=True),
            DagNode(id="plain", members=("z",), cyclic=False),
        ),
        edges=(
            DagEdge(
                source="cycle",
                target="plain",
                raw_dependencies=(
                    RawDependency("b", "z"),
                    RawDependency("a", "z"),
                ),
            ),
        ),
        dependency_first_layers=(("plain",), ("cycle",)),
    )
    # Raw input fields deliberately contain None: the renderer must use the
    # stored quality and DAG, without inspecting facts or rebuilding topology.
    result = _result(
        dag=dag,
        modules=None,
        dependencies=None,
        import_facts=None,
        diagnostics=None,
    )

    expected = """# Python dependency DAG

Generated file.

## Architecture quality

**Architecture score: unavailable (no modules) (experimental, architecture-v1)**

The score uses raw module dependencies in every diagram view. Higher is better under this heuristic; isolated modules do not affect it.

| Metric | Value |
| --- | --- |
| Cycle avoidance (70%) | unavailable |
| Dependency isolation (30%) | unavailable |
| Modules: total / active / isolated | 0 / 0 / 0 |
| Internal dependencies | 0 |
| Cyclic components / cyclic modules / largest cycle | 0 / 0 / 0 |
| Reachable ordered pairs (excluding self) | 0 |
| Maximum fan-in / fan-out | 0 / 0 |
| Unresolved import records | 0 |
| Dynamic-import warnings | 0 |

Compare runs with the same source root, exclusions and analyser/formula versions. This experimental score measures dependency structure, not overall code quality.

Legend: `A -> B` means A contains an import statically resolved to B. Each node is one module; a node with several members is a strongly connected component. Subgraphs are dependency-first layers, so an edge always points down the page.

```mermaid
flowchart TD
    subgraph layer1["Layer 1"]
        n0001["Cycle (2): a&#92;, b&quot;&amp;&lt;&#10;"]:::cycle
    end
    subgraph layer0["Layer 0"]
        n0002["z"]
    end
    n0001 -->|2 imports| n0002
    classDef cycle fill:#fff1f2,stroke:#be123c,stroke-width:2px
```
"""
    assert render_mermaid_markdown(result) == expected
    assert render_mermaid_markdown(result) == expected


def test_mermaid_warns_only_above_advisory_threshold_without_truncating() -> None:
    def dag_with_size(size: int) -> Dag:
        return Dag(
            nodes=tuple(
                DagNode(id=f"scc-{index}", members=(f"m{index:03d}",), cyclic=False)
                for index in range(size)
            ),
            edges=(),
            dependency_first_layers=(),
        )

    at_threshold = render_mermaid_markdown(_result(dag=dag_with_size(200)))
    above_threshold = render_mermaid_markdown(_result(dag=dag_with_size(201)))

    assert "Warning:" not in at_threshold
    assert "Warning:" in above_threshold
    assert "201 nodes" in above_threshold
    assert "--view package" in above_threshold
    assert sum('["m' in line for line in above_threshold.splitlines()) == 201


def test_single_raw_dependency_has_no_edge_count_label() -> None:
    dag = Dag(
        nodes=(
            DagNode("a", ("a",), False),
            DagNode("b", ("b",), False),
        ),
        edges=(DagEdge("a", "b", (RawDependency("a", "b"),)),),
        dependency_first_layers=(("b",), ("a",)),
    )

    rendered = render_mermaid_markdown(_result(dag=dag))
    assert "n0001 --> n0002" in rendered
    assert "|1 import" not in rendered


def _implied_dag() -> Dag:
    """a -> b -> c plus a direct a -> c that the longer path implies."""
    return Dag(
        nodes=(
            DagNode("a", ("a",), False),
            DagNode("b", ("b",), False),
            DagNode("c", ("c",), False),
        ),
        edges=(
            DagEdge("a", "b", (RawDependency("a", "b"),)),
            DagEdge("b", "c", (RawDependency("b", "c"),)),
            DagEdge(
                "a",
                "c",
                (RawDependency("a1", "c"), RawDependency("a2", "c")),
            ),
        ),
        dependency_first_layers=(("c",), ("b",), ("a",)),
    )


def test_implied_edges_are_dotted_by_default_not_dropped() -> None:
    rendered = render_mermaid_markdown(_result(dag=_implied_dag()))

    # a -> c is implied by a -> b -> c, and carries two imports.
    assert "n0001 -.->|2 imports| n0003" in rendered
    assert "n0001 --> n0002" in rendered
    assert "n0002 --> n0003" in rendered
    assert "drawn dotted" in rendered


def test_solid_mode_draws_every_edge_alike() -> None:
    rendered = render_mermaid_markdown(
        _result(dag=_implied_dag()), implied_edges=ImpliedEdges.SOLID
    )

    assert "n0001 -->|2 imports| n0003" in rendered
    assert "-." not in rendered
    assert "All edges are drawn alike" in rendered


def test_omit_mode_drops_the_implied_edge_and_says_so() -> None:
    rendered = render_mermaid_markdown(
        _result(dag=_implied_dag()), implied_edges=ImpliedEdges.OMIT
    )

    assert "n0001 -" not in rendered.split("n0001 --> n0002")[1]
    assert "n0003" in rendered  # the node survives; only the edge goes
    assert "are not drawn" in rendered
    assert (
        "understates coupling" in rendered or "carry most of the coupling" in rendered
    )


def test_a_single_import_implied_edge_is_dotted_without_a_label() -> None:
    dag = Dag(
        nodes=(
            DagNode("a", ("a",), False),
            DagNode("b", ("b",), False),
            DagNode("c", ("c",), False),
        ),
        edges=(
            DagEdge("a", "b", (RawDependency("a", "b"),)),
            DagEdge("b", "c", (RawDependency("b", "c"),)),
            DagEdge("a", "c", (RawDependency("a", "c"),)),
        ),
        dependency_first_layers=(("c",), ("b",), ("a",)),
    )

    rendered = render_mermaid_markdown(_result(dag=dag))

    assert "n0001 -.-> n0003" in rendered


def test_no_implied_note_when_every_edge_is_essential() -> None:
    dag = Dag(
        nodes=(DagNode("a", ("a",), False), DagNode("b", ("b",), False)),
        edges=(DagEdge("a", "b", (RawDependency("a", "b"),)),),
        dependency_first_layers=(("b",), ("a",)),
    )

    rendered = render_mermaid_markdown(_result(dag=dag))

    assert "implied by a longer path" not in rendered
    assert "-." not in rendered
