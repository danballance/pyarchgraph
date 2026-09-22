from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import random
import sys

import networkx as nx
import pytest

from pyarchgraph import GraphPolicy, analyse, render_json
from pyarchgraph.cleanup import build_cleanup_report
from pyarchgraph.model import View


EXAMPLES = Path(__file__).parents[1] / "examples" / "projects"


def _document(root: Path, **kwargs) -> dict:
    return json.loads(render_json(analyse(root, **kwargs)))


def _write(root: Path, sources: dict[str, str]) -> None:
    for name, text in sources.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize(
    "name",
    [
        "definite_cycle",
        "cycle_with_independent_pair",
        "cycle_with_49_pairs",
        "cycle_with_test_padding",
        "cycle_with_isolated_modules",
    ],
)
def test_unrelated_code_never_dilutes_cycle_debt(name: str) -> None:
    cleanup = _document(EXAMPLES / name, policy=GraphPolicy(include_tests=True))[
        "cleanup"
    ]
    assert cleanup["model_version"] == "policy-debt-v1"
    assert cleanup["violation_count"] == 2
    assert cleanup["counts"] == {"cyclic_dependency": 2, "forbidden_dependency": 0}
    assert cleanup["possible_violation_count"] == 0
    assert not cleanup["cleanup_complete"]
    assert {(item["source"], item["target"]) for item in cleanup["violations"]} == {
        ("orders", "inventory"),
        ("inventory", "orders"),
    }


def test_boundaries_catch_shortcuts_without_penalizing_permitted_density() -> None:
    root = EXAMPLES / "dense_ordered_dag"
    permitted = _document(root)
    forbidden = _document(
        root, forbidden_dependencies=(("presentation", "repository"),)
    )
    assert permitted["quality"]["score"] == forbidden["quality"]["score"] == 85
    assert permitted["cleanup"]["cleanup_complete"]
    assert permitted["cleanup"]["violation_count"] == 0
    assert forbidden["cleanup"]["counts"] == {
        "cyclic_dependency": 0,
        "forbidden_dependency": 1,
    }


def test_duplicate_evidence_and_rules_do_not_duplicate_obligations(
    tmp_path: Path,
) -> None:
    _write(tmp_path, {"a.py": "import a\nimport a\n"})
    cleanup = _document(
        tmp_path, forbidden_dependencies=(("a", "a"), ("*", "a"), ("a", "a"))
    )["cleanup"]
    assert cleanup["violation_count"] == 2
    cycle, forbidden = cleanup["violations"]
    assert cycle["kind"] == "cyclic_dependency"
    assert forbidden["rules"] == [["*", "a"], ["a", "a"]]
    assert len(cycle["evidence"]) == len(forbidden["evidence"]) == 2
    assert len({item["id"] for item in cleanup["violations"]}) == 2


def test_scc_merge_and_split_do_not_use_component_count_as_debt(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "a.py": "import b\n",
            "b.py": "import a\n",
            "c.py": "import d\n",
            "d.py": "import c\n",
        },
    )
    separate = _document(tmp_path)["cleanup"]
    _write(tmp_path, {"b.py": "import a\nimport c\n", "d.py": "import c\nimport a\n"})
    merged = _document(tmp_path)["cleanup"]
    _write(tmp_path, {"d.py": "import c\n"})
    split = _document(tmp_path)["cleanup"]
    assert [item["violation_count"] for item in (separate, merged, split)] == [4, 6, 4]
    assert [len(item["work_items"]) for item in (separate, merged, split)] == [2, 1, 2]


def test_mixed_component_preserves_per_dependency_certainty(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/a.py": "import pkg.b\n",
            "pkg/b.py": "import pkg.a\nfrom . import c\n",
            "pkg/c.py": "import pkg.b\n",
        },
    )
    document = _document(tmp_path)
    assert document["findings"][0]["certainty"] == "definite"
    cleanup = document["cleanup"]
    assert cleanup["violation_count"] == cleanup["possible_violation_count"] == 2
    assert cleanup["work_items"][0]["modules"] == ["pkg.a", "pkg.b"]
    assert {
        (item["source"], item["target"])
        for item in cleanup["violations"]
        if item["certainty"] == "possible"
    } == {("pkg.b", "pkg.c"), ("pkg.c", "pkg.b")}


@pytest.mark.parametrize(
    "name",
    ["shadowed_package_attribute", "missing_internal_target", "dynamic_nonliteral"],
)
def test_zero_known_debt_does_not_clear_uncertainty(name: str) -> None:
    cleanup = _document(EXAMPLES / name)["cleanup"]
    assert cleanup["violation_count"] == 0
    assert not cleanup["cleanup_complete"]
    assert not cleanup["coverage"]["dependency_resolution_complete"]


def test_partial_scope_and_empty_inventory_keep_honest_counts(tmp_path: Path) -> None:
    assert not _document(tmp_path)["cleanup"]["cleanup_complete"]
    _write(
        tmp_path,
        {"a.py": "import b\n", "b.py": "import a\n", "bad.py": "def broken(:\n"},
    )
    cleanup = _document(tmp_path)["cleanup"]
    assert cleanup["violation_count"] == 2
    assert not cleanup["coverage"]["complete"]
    assert cleanup["coverage"]["diagnostics"]
    (tmp_path / "bad.py").unlink()
    cleanup = _document(tmp_path, expected_packages=("missing",))["cleanup"]
    assert cleanup["violation_count"] == 2
    assert not cleanup["coverage"]["scope_valid"]
    assert not cleanup["cleanup_complete"]


def test_identity_survives_locations_and_projection(tmp_path: Path) -> None:
    _write(tmp_path, {"a.py": "import b\n", "b.py": "import a\n"})
    original = _document(tmp_path)["cleanup"]
    projected = _document(tmp_path, view=View.PACKAGE, package_depth=1)["cleanup"]
    assert original == projected
    _write(tmp_path, {"a.py": "\n\nimport b\n"})
    moved = _document(tmp_path)["cleanup"]
    assert [item["id"] for item in original["violations"]] == [
        item["id"] for item in moved["violations"]
    ]
    assert original["violations"][0]["evidence"] != moved["violations"][0]["evidence"]


def test_work_items_have_bounded_witnesses_and_source_evidence() -> None:
    cleanup = _document(EXAMPLES / "dense_cyclic_component")["cleanup"]
    by_id = {item["id"]: item for item in cleanup["violations"]}
    assert cleanup["work_items"] == sorted(
        cleanup["work_items"], key=lambda item: (-item["violation_count"], item["id"])
    )
    for item in cleanup["work_items"]:
        assert item["violation_count"] == len(item["violation_ids"])
        assert 1 <= len(item["witness"]) <= len(item["modules"])
        witness = [by_id[identity] for identity in item["witness"]]
        for index, edge in enumerate(witness):
            assert edge["target"] == witness[(index + 1) % len(witness)]["source"]
            assert edge["evidence"][0]["path"]
            assert edge["evidence"][0]["line"] >= 1


@pytest.mark.skipif(sys.version_info < (3, 12), reason="type alias syntax")
def test_type_aliases_preserve_coverage_and_uncertainty(tmp_path: Path) -> None:
    _write(tmp_path, {"a.py": "type Alias[T: int] = list[T]\n", "b.py": "import a\n"})
    assert _document(tmp_path)["cleanup"]["cleanup_complete"]
    _write(
        tmp_path,
        {
            "a.py": "from importlib import import_module\ntype Alias = import_module('b')\n"
        },
    )
    cleanup = _document(tmp_path)["cleanup"]
    assert cleanup["violation_count"] == 0
    assert cleanup["possible_violation_count"] == 2
    assert not cleanup["cleanup_complete"]
    _write(tmp_path, {"a.py": "def function[T: int]():\n    pass\n"})
    cleanup = _document(tmp_path)["cleanup"]
    assert not cleanup["coverage"]["complete"]
    assert not cleanup["cleanup_complete"]


def test_random_graph_debt_matches_independent_return_path_oracle() -> None:
    # Testing the graph model directly isolates counting from extraction.
    rng = random.Random(421)
    for _ in range(100):
        names = list("abcdef")
        pairs = {(a, b) for a in names for b in names if rng.random() < 0.2}
        graph = nx.DiGraph()
        graph.add_nodes_from(names)
        graph.add_edges_from(pairs)

        def document(selected):
            return {
                "analysis": {
                    "complete": True,
                    "scope_valid": True,
                    "dependency_resolution_complete": True,
                },
                "modules": [{"id": name} for name in names],
                "import_facts": [{"id": a + b} for a, b in selected],
                "architecture_dependencies": [
                    {
                        "source": a,
                        "target": b,
                        "evidence": [
                            {"fact_id": a + b, "resolution_kind": "exact_module"}
                        ],
                    }
                    for a, b in selected
                ],
                "check": {"status": "pass"},
            }

        original = document(pairs)
        unchanged = deepcopy(original)
        cleanup = build_cleanup_report(original)
        expected = {(a, b) for a, b in pairs if nx.has_path(graph, b, a)}
        assert {
            (item["source"], item["target"]) for item in cleanup["violations"]
        } == expected
        assert original == unchanged
        added = pairs | {(rng.choice(names), rng.choice(names))}
        assert (
            build_cleanup_report(document(added))["violation_count"]
            >= cleanup["violation_count"]
        )
        if pairs:
            reduced = pairs - {sorted(pairs)[0]}
            assert (
                build_cleanup_report(document(reduced))["violation_count"]
                <= cleanup["violation_count"]
            )


def test_cleanup_never_enumerates_simple_cycles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("Cleanup must use SCCs and bounded witnesses")

    monkeypatch.setattr(nx, "simple_cycles", forbidden)
    document = _document(EXAMPLES / "dense_cyclic_component")
    assert document["cleanup"]["violation_count"] > 0
