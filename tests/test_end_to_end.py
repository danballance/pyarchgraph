from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from pyarchgraph import analyse, render_json


FACT_KEYS = {
    "id",
    "source",
    "path",
    "line",
    "column",
    "end_line",
    "end_column",
    "alias_index",
    "syntax",
    "source_segment",
    "base_module",
    "imported_name",
    "as_name",
    "bound_name",
    "relative_level",
    "scope",
    "type_only",
}


def _write(root: Path, relative_path: str, source: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _run_cli(
    source_root: Path,
    output_dir: Path,
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pyarchgraph",
            str(source_root),
            "--output-dir",
            str(output_dir),
            *extra,
        ],
        cwd=source_root.parent,
        check=False,
        capture_output=True,
        text=True,
    )


def _fact_sort_key(fact: dict[str, Any]) -> tuple[object, ...]:
    return (
        fact["source"],
        fact["path"],
        fact["line"],
        fact["column"],
        fact["end_line"] if fact["end_line"] is not None else -1,
        fact["end_column"] if fact["end_column"] is not None else -1,
        fact["alias_index"],
        fact["syntax"],
        fact["base_module"] or "",
        fact["imported_name"] or "",
        fact["as_name"] or "",
        fact["bound_name"],
        fact["relative_level"],
        fact["scope"],
        fact["type_only"],
        fact["source_segment"] or "",
    )


def _fact_by_segment(
    document: dict[str, Any], source: str, source_segment: str
) -> dict[str, Any]:
    matches = [
        fact
        for fact in document["import_facts"]
        if fact["source"] == source and fact["source_segment"] == source_segment
    ]
    assert len(matches) == 1
    return matches[0]


def _evidence_for(
    document: dict[str, Any], source: str, target: str
) -> list[dict[str, str]]:
    matches = [
        dependency["evidence"]
        for dependency in document["dependencies"]
        if (dependency["source"], dependency["target"]) == (source, target)
    ]
    assert len(matches) == 1
    return matches[0]


def test_canonical_json_matches_reviewed_golden_file() -> None:
    fixture_root = Path(__file__).parent / "fixtures" / "golden_minimal"
    golden_path = Path(__file__).parent / "golden" / "minimal-dependency-graph.json"

    result = replace(analyse(fixture_root), python_version="NORMALIZED")

    assert render_json(result) == golden_path.read_text(encoding="utf-8")


def test_representative_source_tree_satisfies_the_complete_output_contract(
    tmp_path: Path,
) -> None:
    """Exercise the public CLI over one deliberately adversarial source tree."""

    source_root = tmp_path / "project" / "src"
    output_dir = tmp_path / "output"

    _write(source_root, "namespace/deep.py", "VALUE = 'namespace leaf'\n")
    _write(source_root, "pkg/__init__.py", "from . import leaf\n")
    _write(
        source_root,
        "pkg/app.py",
        """from typing import TYPE_CHECKING
import typing
from typing import TYPE_CHECKING as TC
import typing as ty
import sys
import importlib
import importlib as il

import pkg.base as base_alias
import pkg.base
from pkg.base import Thing
from pkg.base import *
from pkg import leaf as chosen_leaf
import namespace.deep
from namespace import deep
import namespace.absent
import pkg.missing
import json as json_alias
import definitely_external as unknown

if TYPE_CHECKING:
    import pkg.type_target
if typing.TYPE_CHECKING:
    import pkg.type_leaf
if TC:
    from pkg import type_target
else:
    import pkg.else_target
if ty.TYPE_CHECKING:
    from pkg import type_leaf
if TC and True:
    import pkg.boolean_target
if sys.platform == "never":
    import pkg.conditional

def load_lazily():
    import pkg.local_target
    return pkg.local_target

dynamic_name = "pkg.dynamic"
importlib.import_module("pkg.dynamic")
importlib.import_module(dynamic_name)
__import__("pkg.dynamic")
__import__(dynamic_name)
il.import_module("pkg.alias_dynamic_is_ignored")
""",
    )
    _write(source_root, "pkg/base.py", "class Thing:\n    pass\n")
    for module_name in (
        "boolean_target",
        "conditional",
        "else_target",
        "isolated",
        "leaf",
        "local_target",
        "type_leaf",
        "type_target",
    ):
        _write(source_root, f"pkg/{module_name}.py", "VALUE = 1\n")
    _write(
        source_root,
        "pkg/cycle_a.py",
        "import pkg.cycle_b\nimport pkg.base\n",
    )
    _write(
        source_root,
        "pkg/cycle_b.py",
        "import pkg.cycle_a\nimport pkg.base\n",
    )
    _write(source_root, "pkg/self_loop.py", "import pkg.self_loop\n")
    _write(source_root, "pkg/sub/__init__.py", "VALUE = 'subpackage'\n")
    _write(source_root, "pkg/sub/helper.py", "VALUE = 'helper'\n")
    _write(
        source_root,
        "pkg/sub/worker.py",
        """from . import helper
from .helper import VALUE
from .. import leaf
from ..base import Thing
from pkg.base import *
from ... import outside
""",
    )
    _write(source_root, "top_level.py", "from . import impossible\n")
    _write(source_root, "pkg/broken.py", "def malformed(:\n")
    undecodable = source_root / "pkg" / "undecodable.py"
    undecodable.write_bytes(b"# coding: utf-8\n\xff\n")

    execution_marker = source_root / "pkg" / "EXECUTED"
    _write(
        source_root,
        "pkg/destructive.py",
        """from pathlib import Path
Path(__file__).with_name("EXECUTED").write_text("target code ran")
raise RuntimeError("target code must never execute")
""",
    )

    first_run = _run_cli(source_root, output_dir)

    assert first_run.returncode == 1, first_run.stderr
    assert first_run.stdout == ""
    assert "22 modules" in first_run.stderr
    assert "2 cyclic components" in first_run.stderr
    assert "6 diagnostics" in first_run.stderr
    assert not execution_marker.exists()

    json_path = output_dir / "dependency-graph.json"
    markdown_path = output_dir / "dependency-dag.md"
    json_bytes = json_path.read_bytes()
    markdown_bytes = markdown_path.read_bytes()
    document = json.loads(json_bytes)
    markdown = markdown_bytes.decode("utf-8")

    # The public JSON boundary has the complete declared v0.2 shape.
    assert set(document) == {
        "schema_version",
        "quality",
        "semantics",
        "analysis",
        "modules",
        "import_facts",
        "dependencies",
        "external_imports",
        "unresolved_imports",
        "dag",
        "diagnostics",
    }
    assert document["schema_version"] == "0.2"
    assert document["quality"]["score"] is None
    assert document["quality"]["unavailable_reason"] == "incomplete_analysis"
    assert document["quality"]["metrics"]["module_count"] == 22
    assert document["quality"]["dynamic_import_warning_count"] == 4
    assert document["semantics"] == {
        "dynamic_imports": "diagnosed_not_resolved",
        "edge_direction": "importer_to_imported",
        "edge_kind": "syntactic_import",
        "implicit_parent_package_imports": False,
        "namespace_packages": "prefixes_known_nodes_not_emitted",
        "node_kind": "python_module",
    }
    assert set(document["analysis"]) == {
        "excludes",
        "complete",
        "python_version",
        "namespace_prefixes",
        "view",
    }
    assert document["analysis"]["complete"] is False
    assert re.fullmatch(r"\d+\.\d+\.\d+", document["analysis"]["python_version"])
    assert document["analysis"]["namespace_prefixes"] == ["namespace"]
    assert document["analysis"]["view"] == {"kind": "module"}
    assert str(source_root) not in json_bytes.decode("utf-8")
    assert json_bytes.endswith(b"\n") and not json_bytes.endswith(b"\n\n")

    modules = document["modules"]
    module_ids = [module["id"] for module in modules]
    assert module_ids == sorted(module_ids)
    assert len(module_ids) == 22
    assert "pkg" in module_ids
    assert "pkg.__init__" not in module_ids
    assert "namespace.deep" in module_ids
    assert "namespace" not in module_ids
    assert "top_level" in module_ids
    assert all(not module_id.startswith("src.") for module_id in module_ids)
    assert all(
        set(module) == {"id", "path", "is_package", "parent_package"}
        for module in modules
    )
    package = next(module for module in modules if module["id"] == "pkg")
    assert package == {
        "id": "pkg",
        "path": "pkg/__init__.py",
        "is_package": True,
        "parent_package": None,
    }

    facts = document["import_facts"]
    assert facts == sorted(facts, key=_fact_sort_key)
    assert all(set(fact) == FACT_KEYS for fact in facts)
    assert {fact["syntax"] for fact in facts} == {"import", "import_from"}
    assert {fact["scope"] for fact in facts} == {"module", "local"}
    fact_ids = [fact["id"] for fact in facts]
    assert len(fact_ids) == len(set(fact_ids))
    assert all(re.fullmatch(r"fact-[0-9a-f]{12,64}", fact_id) for fact_id in fact_ids)
    facts_by_id = {fact["id"]: fact for fact in facts}

    aliased_absolute = _fact_by_segment(
        document, "pkg.app", "import pkg.base as base_alias"
    )
    assert (
        aliased_absolute["base_module"],
        aliased_absolute["as_name"],
        aliased_absolute["bound_name"],
    ) == ("pkg.base", "base_alias", "base_alias")

    local = _fact_by_segment(document, "pkg.app", "import pkg.local_target")
    conditional = _fact_by_segment(document, "pkg.app", "import pkg.conditional")
    assert (local["scope"], local["type_only"]) == ("local", False)
    assert (conditional["scope"], conditional["type_only"]) == ("module", False)

    for segment in (
        "import pkg.type_target",
        "import pkg.type_leaf",
        "from pkg import type_target",
        "from pkg import type_leaf",
    ):
        assert _fact_by_segment(document, "pkg.app", segment)["type_only"] is True
    assert (
        _fact_by_segment(document, "pkg.app", "import pkg.else_target")["type_only"]
        is False
    )
    assert (
        _fact_by_segment(document, "pkg.app", "import pkg.boolean_target")["type_only"]
        is False
    )

    relative_expectations = {
        "from . import helper": (None, "helper", 1),
        "from .helper import VALUE": ("helper", "VALUE", 1),
        "from .. import leaf": (None, "leaf", 2),
        "from ..base import Thing": ("base", "Thing", 2),
        "from ... import outside": (None, "outside", 3),
    }
    for segment, expected in relative_expectations.items():
        fact = _fact_by_segment(document, "pkg.sub.worker", segment)
        assert (
            fact["base_module"],
            fact["imported_name"],
            fact["relative_level"],
        ) == expected
    top_level_relative = _fact_by_segment(
        document, "top_level", "from . import impossible"
    )
    assert top_level_relative["relative_level"] == 1

    # One raw edge retains every duplicate exact/exact-base source site.
    base_evidence = _evidence_for(document, "pkg.app", "pkg.base")
    assert len(base_evidence) == 4
    assert [
        (facts_by_id[item["fact_id"]]["source_segment"], item["resolution_kind"])
        for item in base_evidence
    ] == sorted(
        [
            ("import pkg.base as base_alias", "exact_module"),
            ("import pkg.base", "exact_module"),
            ("from pkg.base import Thing", "exact_base"),
            ("from pkg.base import *", "exact_base"),
        ],
        key=lambda item: next(
            evidence["fact_id"]
            for evidence in base_evidence
            if facts_by_id[evidence["fact_id"]]["source_segment"] == item[0]
        ),
    )

    ambiguous_from = _fact_by_segment(
        document, "pkg.app", "from pkg import leaf as chosen_leaf"
    )
    assert {
        (
            dependency["target"],
            evidence["resolution_kind"],
        )
        for dependency in document["dependencies"]
        for evidence in dependency["evidence"]
        if evidence["fact_id"] == ambiguous_from["id"]
    } == {("pkg", "exact_base"), ("pkg.leaf", "probable_submodule")}

    wildcard = _fact_by_segment(document, "pkg.sub.worker", "from pkg.base import *")
    assert [
        (dependency["target"], evidence["resolution_kind"])
        for dependency in document["dependencies"]
        for evidence in dependency["evidence"]
        if evidence["fact_id"] == wildcard["id"]
    ] == [("pkg.base", "exact_base")]

    relative_helper = _fact_by_segment(
        document, "pkg.sub.worker", "from . import helper"
    )
    assert {
        (dependency["target"], evidence["resolution_kind"])
        for dependency in document["dependencies"]
        for evidence in dependency["evidence"]
        if evidence["fact_id"] == relative_helper["id"]
    } == {
        ("pkg.sub", "exact_base"),
        ("pkg.sub.helper", "probable_submodule"),
    }

    # Inventory-only classification distinguishes namespaces, missing internals,
    # standard-library modules, and unknown external names.
    assert all(
        set(item) == {"source", "requested", "classification", "fact_ids"}
        for item in document["external_imports"]
    )
    external_classifications = {
        (item["source"], item["requested"]): item["classification"]
        for item in document["external_imports"]
    }
    assert external_classifications == {
        ("pkg.app", "definitely_external"): "external_unknown",
        ("pkg.app", "importlib"): "stdlib",
        ("pkg.app", "json"): "stdlib",
        ("pkg.app", "sys"): "stdlib",
        ("pkg.app", "typing"): "stdlib",
        ("pkg.destructive", "pathlib"): "stdlib",
    }
    assert all(
        set(item) == {"source", "requested", "reason", "fact_ids"}
        for item in document["unresolved_imports"]
    )
    assert {
        (item["source"], item["requested"], item["reason"])
        for item in document["unresolved_imports"]
    } == {
        ("pkg.app", "namespace", "namespace_base_unmodelled"),
        ("pkg.app", "namespace.absent", "missing_internal_target"),
        ("pkg.app", "pkg.missing", "missing_internal_target"),
        ("pkg.sub.worker", "...", "relative_escape"),
        ("top_level", ".", "relative_escape"),
    }
    namespace_fact = _fact_by_segment(document, "pkg.app", "from namespace import deep")
    assert any(
        evidence["fact_id"] == namespace_fact["id"]
        and dependency["target"] == "namespace.deep"
        and evidence["resolution_kind"] == "probable_submodule"
        for dependency in document["dependencies"]
        for evidence in dependency["evidence"]
    )
    assert any(
        namespace_fact["id"] in item["fact_ids"]
        and item["reason"] == "namespace_base_unmodelled"
        for item in document["unresolved_imports"]
    )

    # Every static fact is traceable from an edge or an explicit classification.
    referenced_fact_ids = {
        evidence["fact_id"]
        for dependency in document["dependencies"]
        for evidence in dependency["evidence"]
    }
    referenced_fact_ids.update(
        fact_id
        for collection_name in ("external_imports", "unresolved_imports")
        for item in document[collection_name]
        for fact_id in item["fact_ids"]
    )
    assert referenced_fact_ids == set(fact_ids)
    for dependency in document["dependencies"]:
        assert set(dependency) == {"source", "target", "evidence"}
        assert dependency["source"] in module_ids
        assert dependency["target"] in module_ids
        assert dependency["evidence"] == sorted(
            dependency["evidence"],
            key=lambda item: (item["fact_id"], item["resolution_kind"]),
        )
        assert all(
            facts_by_id[evidence["fact_id"]]["source"] == dependency["source"]
            for evidence in dependency["evidence"]
        )
    for collection_name in ("external_imports", "unresolved_imports"):
        assert all(
            item["fact_ids"] == sorted(item["fact_ids"])
            for item in document[collection_name]
        )

    # Raw cycles are preserved, while the exported graph is their condensation.
    dag = document["dag"]
    assert set(dag) == {"nodes", "edges", "dependency_first_layers"}
    assert all(set(node) == {"id", "members", "cyclic"} for node in dag["nodes"])
    assert all(
        set(edge) == {"source", "target", "raw_dependencies"} for edge in dag["edges"]
    )
    flattened_members = [member for node in dag["nodes"] for member in node["members"]]
    assert sorted(flattened_members) == module_ids
    assert len(flattened_members) == len(set(flattened_members))
    cycle = next(
        node
        for node in dag["nodes"]
        if set(node["members"]) == {"pkg.cycle_a", "pkg.cycle_b"}
    )
    self_loop = next(
        node for node in dag["nodes"] if node["members"] == ["pkg.self_loop"]
    )
    isolated = next(
        node for node in dag["nodes"] if node["members"] == ["pkg.isolated"]
    )
    broken = next(node for node in dag["nodes"] if node["members"] == ["pkg.broken"])
    assert cycle["cyclic"] is True
    assert self_loop["cyclic"] is True
    assert isolated["cyclic"] is False
    assert broken["cyclic"] is False

    component_for = {
        member: node["id"] for node in dag["nodes"] for member in node["members"]
    }
    condensed_raw_dependencies = [
        (raw["source"], raw["target"])
        for edge in dag["edges"]
        for raw in edge["raw_dependencies"]
    ]
    for dependency in document["dependencies"]:
        raw = (dependency["source"], dependency["target"])
        if component_for[raw[0]] == component_for[raw[1]]:
            assert raw not in condensed_raw_dependencies
        else:
            assert condensed_raw_dependencies.count(raw) == 1
    assert all(edge["source"] != edge["target"] for edge in dag["edges"])
    layer_index = {
        component_id: index
        for index, layer in enumerate(dag["dependency_first_layers"])
        for component_id in layer
    }
    assert set(layer_index) == {node["id"] for node in dag["nodes"]}
    assert all(layer == sorted(layer) for layer in dag["dependency_first_layers"])
    assert all(
        layer_index[edge["target"]] < layer_index[edge["source"]]
        for edge in dag["edges"]
    )

    diagnostic_codes = [item["code"] for item in document["diagnostics"]]
    assert diagnostic_codes.count("dynamic_import_ignored") == 4
    assert diagnostic_codes.count("source_decode_error") == 1
    assert diagnostic_codes.count("source_syntax_error") == 1
    assert all(
        {"severity", "code", "message"}
        <= set(item)
        <= {"severity", "code", "message", "path", "line", "column"}
        for item in document["diagnostics"]
    )
    assert {item["severity"] for item in document["diagnostics"]} == {
        "error",
        "warning",
    }
    assert all(
        str(source_root) not in item["message"] for item in document["diagnostics"]
    )

    # Mermaid shows every condensation node; nodes inside a layer subgraph are
    # indented one level further than an unlayered one.
    mermaid_node_lines = re.findall(
        r'^ +n\d{4}\[".*"\](?:\:\:\:cycle)?$', markdown, re.MULTILINE
    )
    mermaid_edge_lines = re.findall(r"^    n\d{4} .* n\d{4}$", markdown, re.MULTILINE)
    assert len(mermaid_node_lines) == len(dag["nodes"])
    # The default hides nothing: every condensation edge is drawn, and the ones
    # a longer path implies are dotted rather than dropped.
    assert len(mermaid_edge_lines) == len(dag["edges"])
    assert "flowchart TD" in markdown
    dotted = re.findall(r"^    n\d{4} -\.->.* n\d{4}$", markdown, re.MULTILINE)
    assert dotted, "this tree has implied edges, so some should be dotted"

    # ...and the reduction proper is still available.
    omitted_dir = output_dir.parent / "omitted"
    omitted_run = _run_cli(source_root, omitted_dir, "--implied-edges", "omit")
    assert omitted_run.returncode == 1, omitted_run.stderr
    omitted = (omitted_dir / "dependency-dag.md").read_text(encoding="utf-8")
    omitted_edges = re.findall(r"^    n\d{4} .* n\d{4}$", omitted, re.MULTILINE)
    assert len(omitted_edges) == len(dag["edges"]) - len(dotted)
    assert not re.findall(r"^    n\d{4} -\.->.* n\d{4}$", omitted, re.MULTILINE)
    assert markdown.count(":::cycle") == 2
    assert "Cycle (2): pkg.cycle_a, pkg.cycle_b" in markdown
    assert "pkg.isolated" in markdown
    assert "pkg.broken" in markdown
    assert "classDef cycle" in markdown

    # Re-running the actual command produces byte-identical canonical artifacts.
    second_run = _run_cli(source_root, output_dir)
    assert second_run.returncode == 1, second_run.stderr
    assert not execution_marker.exists()
    assert json_path.read_bytes() == json_bytes
    assert markdown_path.read_bytes() == markdown_bytes
