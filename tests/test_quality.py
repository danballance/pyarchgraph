from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import random

import networkx as nx
import pytest

from pyarchgraph import ArchitectureMetrics, ArchitectureQuality, analyse
from pyarchgraph.model import DependencyEdge, SourceModule, View
from pyarchgraph.quality import calculate_quality
from pyarchgraph.rendering import ImpliedEdges, render_json, render_mermaid_markdown


def _quality(
    names: str,
    pairs: tuple[tuple[str, str], ...],
    *,
    complete: bool = True,
) -> ArchitectureQuality:
    return calculate_quality(
        tuple(SourceModule(name, f"{name}.py", False, None) for name in names),
        tuple(DependencyEdge(source, target, ()) for source, target in pairs),
        complete=complete,
    )


@pytest.mark.parametrize(
    ("names", "pairs", "active", "cyclic", "largest", "reachable", "score"),
    [
        ("a", (), 0, 0, 0, 0, 100.0),
        ("abcd", (), 0, 0, 0, 0, 100.0),
        ("a", (("a", "a"),), 1, 1, 1, 0, 30.0),
        ("ab", (("a", "b"), ("b", "a")), 2, 2, 2, 2, 0.0),
        ("abc", (("a", "b"), ("b", "c")), 3, 0, 0, 3, 85.0),
        ("abcd", (("a", "d"), ("b", "d"), ("c", "d")), 4, 0, 0, 3, 92.5),
        (
            "abcd",
            (("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")),
            4,
            0,
            0,
            5,
            87.5,
        ),
        (
            "abcd",
            (("a", "b"), ("b", "c"), ("c", "a"), ("c", "d")),
            4,
            3,
            3,
            9,
            25.0,
        ),
        (
            "abcd",
            (("a", "b"), ("b", "a"), ("b", "c"), ("c", "d"), ("d", "c")),
            4,
            4,
            2,
            8,
            10.0,
        ),
    ],
)
def test_known_dependency_structures(
    names,
    pairs,
    active,
    cyclic,
    largest,
    reachable,
    score,
) -> None:
    quality = _quality(names, pairs)
    assert quality.formula_version == "architecture-v1"
    assert quality.unavailable_reason is None
    assert quality.score == pytest.approx(score)
    assert quality.metrics.active_module_count == active
    assert quality.metrics.cyclic_module_count == cyclic
    assert quality.metrics.largest_cycle_size == largest
    assert quality.metrics.reachable_pair_count == reachable


def test_counts_and_component_scores_explain_the_headline() -> None:
    quality = _quality("abcd", (("a", "b"), ("b", "a"), ("b", "c")))
    assert isinstance(quality, ArchitectureQuality)
    assert isinstance(quality.metrics, ArchitectureMetrics)
    assert quality.metrics.module_count == 4
    assert quality.metrics.active_module_count == 3
    assert quality.metrics.isolated_module_count == 1
    assert quality.metrics.dependency_count == 3
    assert quality.metrics.cyclic_component_count == 1
    assert quality.metrics.max_fan_in == 1
    assert quality.metrics.max_fan_out == 2
    assert quality.metrics.cycle_fraction == pytest.approx(2 / 3)
    assert quality.metrics.reach_fraction == pytest.approx(4 / 6)
    assert quality.cycle_avoidance_score == pytest.approx(100 / 3)
    assert quality.dependency_isolation_score == pytest.approx(100 / 3)
    assert quality.score == pytest.approx(100 / 3)
    with pytest.raises(FrozenInstanceError):
        quality.score = 0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        quality.metrics.module_count = 0  # type: ignore[misc]


@pytest.mark.parametrize(
    ("names", "pairs", "complete", "reason", "edge_count"),
    [
        ("", (), True, "no_modules", 0),
        ("", (), False, "incomplete_analysis", 0),
        ("ab", (("a", "b"),), False, "incomplete_analysis", 1),
    ],
)
def test_empty_or_incomplete_analysis_has_no_score(
    names,
    pairs,
    complete,
    reason,
    edge_count,
) -> None:
    quality = _quality(names, pairs, complete=complete)
    assert quality.unavailable_reason == reason
    assert quality.score is None
    assert quality.cycle_avoidance_score is None
    assert quality.dependency_isolation_score is None
    assert quality.metrics.dependency_count == edge_count


def test_removing_cycle_improves_score_and_isolates_cannot_dilute_it() -> None:
    chain = (("a", "b"), ("b", "c"))
    cycle = (*chain, ("c", "a"))
    assert _quality("abc", chain).score > _quality("abc", cycle).score
    original = _quality("abc", cycle)
    padded = _quality("abcdef", cycle)
    assert padded.score == original.score
    assert padded.metrics.isolated_module_count == 3
    assert padded.metrics.cycle_fraction == original.metrics.cycle_fraction
    assert padded.metrics.reach_fraction == original.metrics.reach_fraction


def test_duplicate_edges_and_input_order_do_not_change_metrics() -> None:
    edges = (("a", "b"), ("a", "c"), ("c", "d"), ("d", "c"))
    assert _quality("abcd", edges) == _quality("dcba", (*reversed(edges), *edges))


def test_redundant_direct_edge_changes_edge_count_but_not_reach_score() -> None:
    before = _quality("abc", (("a", "b"), ("b", "c")))
    after = _quality("abc", (("a", "b"), ("b", "c"), ("a", "c")))
    assert before.score == after.score
    assert before.metrics.reachable_pair_count == after.metrics.reachable_pair_count
    assert after.metrics.dependency_count == before.metrics.dependency_count + 1
    assert after.metrics.max_fan_out == 2


def test_exact_headline_is_preserved_when_component_fraction_repeats() -> None:
    quality = _quality("abcd", (("a", "b"), ("b", "c"), ("d", "c")))
    assert quality.metrics.reach_fraction == 1 / 3
    assert quality.score == 90.0


def test_reachability_matches_independent_networkx_traversals() -> None:
    rng = random.Random(20260909)
    for _ in range(200):
        names = "abcdefghijklmnopqrstuvwxyz"[: rng.randrange(27)]
        pairs = tuple((a, b) for a in names for b in names if rng.random() < 0.08)
        graph = nx.DiGraph()
        graph.add_nodes_from(names)
        graph.add_edges_from(pairs)
        quality = _quality(names, pairs)
        assert quality.metrics.reachable_pair_count == sum(
            len(nx.descendants(graph, node)) for node in graph
        )
        assert quality.score is None or 0 <= quality.score <= 100


def test_unknown_endpoints_and_duplicate_module_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="indexed modules"):
        _quality("a", (("a", "b"),))
    with pytest.raises(ValueError, match="unique"):
        _quality("aa", ())


def test_analysis_scores_raw_graph_in_every_view_and_edge_style(tmp_path: Path) -> None:
    package = tmp_path / "app"
    package.mkdir()
    (package / "a.py").write_text("import app.b\nimport app.c\n")
    (package / "b.py").write_text("import app.a\nimport app.c\n")
    (package / "c.py").write_text("VALUE = 1\n")
    base = analyse(tmp_path)
    assert base.quality.metrics.cyclic_module_count == 2
    for view, depth in ((View.MODULE, 2), (View.PACKAGE, 1), (View.PACKAGE, 2)):
        result = analyse(tmp_path, view=view, package_depth=depth)
        assert result.quality == base.quality
        if view is View.PACKAGE and depth == 1:
            assert not any(node.cyclic for node in result.dag.nodes)
        assert (
            json.loads(render_json(result))["quality"]
            == json.loads(render_json(base))["quality"]
        )
        for style in ImpliedEdges:
            markdown = render_mermaid_markdown(result, implied_edges=style)
            assert "Architecture score: 33.3/100" in markdown
            assert markdown == render_mermaid_markdown(result, implied_edges=style)


def test_type_only_local_and_probable_imports_keep_existing_semantics(
    tmp_path: Path,
) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "a.py").write_text(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from pkg import b\n"
    )
    (package / "b.py").write_text("def lazy():\n    import pkg.a\n")
    result = analyse(tmp_path)
    assert result.quality.metrics.cyclic_module_count == 2
    assert result.quality.metrics.dependency_count == 3
    assert result.quality.metrics.active_module_count == 3
    original_quality = result.quality
    with (package / "b.py").open("a") as handle:
        handle.write("    import pkg.a\n")
    assert analyse(tmp_path).quality == original_quality


def test_package_projection_cannot_create_a_score_penalty(tmp_path: Path) -> None:
    sources = {
        "app/left/a.py": "import app.right.first\n",
        "app/left/b.py": "VALUE = 1\n",
        "app/right/first.py": "VALUE = 1\n",
        "app/right/second.py": "import app.left.b\n",
    }
    for relative, content in sources.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    modules = analyse(tmp_path)
    packages = analyse(tmp_path, view=View.PACKAGE, package_depth=2)
    assert not any(node.cyclic for node in modules.dag.nodes)
    assert any(node.cyclic for node in packages.dag.nodes)
    assert packages.quality == modules.quality
    assert packages.quality.score == 95.0
