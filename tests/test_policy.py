from __future__ import annotations

from pathlib import Path

import pytest

from pyarchgraph.analysis import analyse
from pyarchgraph.findings import check_status
from pyarchgraph.model import ImportScope
from pyarchgraph.policy import GraphPolicy


def _write(root: Path, files: dict[str, str]) -> None:
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def _cycles(result):
    return [finding for finding in result.findings if finding["kind"] == "cycle"]


def _assert_closed_witness(finding, result) -> None:
    witness = finding["witness"]
    pairs = {(edge.source, edge.target) for edge in result.architecture_dependencies}
    assert 1 <= len(witness) <= len(finding["members"])
    assert len({edge["source"] for edge in witness}) == len(witness)
    for index, edge in enumerate(witness):
        assert (edge["source"], edge["target"]) in pairs
        assert edge["target"] == witness[(index + 1) % len(witness)]["source"]
        assert edge["evidence"]
        for evidence in edge["evidence"]:
            assert evidence["path"]
            assert evidence["line"] >= 1
            assert evidence["source_segment"]


def test_definite_cycle_has_one_closed_witness_with_import_locations(
    tmp_path: Path,
) -> None:
    _write(tmp_path, {"a.py": "# consumer\nimport b\n", "b.py": "import a\n"})

    result = analyse(tmp_path)

    assert check_status(result) == "fail"
    (cycle,) = _cycles(result)
    assert cycle["certainty"] == "definite"
    assert cycle["members"] == cycle["definite_members"] == ["a", "b"]
    _assert_closed_witness(cycle, result)
    assert {
        (edge["source"], evidence["path"], evidence["line"])
        for edge in cycle["witness"]
        for evidence in edge["evidence"]
    } == {("a", "a.py", 2), ("b", "b.py", 1)}


def test_shadowable_child_cycle_requires_review_without_claiming_definite_binding(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        {
            "pkg/__init__.py": "b = 42\n",
            "pkg/a.py": "from pkg import b\n",
            "pkg/b.py": "import pkg.a\n",
        },
    )

    result = analyse(tmp_path)

    (cycle,) = _cycles(result)
    assert cycle["certainty"] == "possible"
    assert cycle["definite_members"] == []
    assert cycle["definite_cyclic_dependencies"] == []
    assert check_status(result) == "needs_review"
    _assert_closed_witness(cycle, result)
    assert any(
        evidence["resolution_kind"] == "probable_submodule"
        for edge in cycle["witness"]
        for evidence in edge["evidence"]
    )


def test_mixed_component_reports_definite_members_and_uses_definite_witness(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/a.py": "import pkg.b\n",
            "pkg/b.py": "import pkg.a\nfrom . import c\n",
            "pkg/c.py": "import pkg.b\n",
        },
    )

    result = analyse(tmp_path)

    (cycle,) = _cycles(result)
    assert cycle["members"] == ["pkg.a", "pkg.b", "pkg.c"]
    assert cycle["definite_members"] == ["pkg.a", "pkg.b"]
    assert cycle["certainty"] == "definite"
    assert cycle["definite_cyclic_dependencies"] == [
        ["pkg.a", "pkg.b"],
        ["pkg.b", "pkg.a"],
    ]
    _assert_closed_witness(cycle, result)
    assert {edge["source"] for edge in cycle["witness"]} == {"pkg.a", "pkg.b"}
    assert all(
        evidence["resolution_kind"] in {"exact_module", "exact_base"}
        for edge in cycle["witness"]
        for evidence in edge["evidence"]
    )


def test_bow_tie_component_size_is_not_a_longest_cycle_claim(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "a.py": "import b\n",
            "b.py": "import a\nimport c\n",
            "c.py": "import b\n",
        },
    )

    result = analyse(tmp_path)

    (cycle,) = _cycles(result)
    assert result.quality.metrics.largest_cyclic_component_size == 3
    assert cycle["members"] == ["a", "b", "c"]
    assert len(cycle["witness"]) == 2
    _assert_closed_witness(cycle, result)


def test_dense_components_still_get_only_one_bounded_witness_each(
    tmp_path: Path,
) -> None:
    files = {}
    for group in ("abcdef", "xyz"):
        for name in group:
            files[f"{name}.py"] = "".join(
                f"import {target}\n" for target in group if target != name
            )
    _write(tmp_path, files)

    result = analyse(tmp_path)

    cycles = _cycles(result)
    assert len(cycles) == 2
    assert [finding["members"] for finding in cycles] == [list("abcdef"), list("xyz")]
    for finding in cycles:
        _assert_closed_witness(finding, result)


def test_self_import_has_a_one_edge_witness(tmp_path: Path) -> None:
    _write(tmp_path, {"a.py": "import a\n"})

    result = analyse(tmp_path)

    (cycle,) = _cycles(result)
    assert cycle["members"] == ["a"]
    assert len(cycle["witness"]) == 1
    _assert_closed_witness(cycle, result)


def test_typing_and_local_filters_keep_independent_ordinary_evidence(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        {
            "a.py": (
                "from typing import TYPE_CHECKING\n"
                "if TYPE_CHECKING:\n    import b\n"
                "def late():\n    import b\n"
                "import b\n"
            ),
            "b.py": "import a\n",
        },
    )

    result = analyse(
        tmp_path, policy=GraphPolicy(include_type_only=False, include_local=False)
    )

    edge = next(edge for edge in result.architecture_dependencies if edge.source == "a")
    facts = {fact.id: fact for fact in result.import_facts}
    assert len(edge.evidence) == 1
    assert facts[edge.evidence[0].fact_id].line == 6
    assert facts[edge.evidence[0].fact_id].scope is ImportScope.MODULE
    assert not facts[edge.evidence[0].fact_id].type_only
    assert check_status(result) == "fail"
    assert (
        len(next(edge for edge in result.dependencies if edge.source == "a").evidence)
        == 3
    )


@pytest.mark.parametrize(
    ("source", "policy"),
    [
        (
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import b\n",
            GraphPolicy(include_type_only=False),
        ),
        ("def late():\n    import b\n", GraphPolicy(include_local=False)),
    ],
)
def test_filtering_only_support_removes_dependency_and_cycle(
    tmp_path: Path,
    source: str,
    policy: GraphPolicy,
) -> None:
    _write(tmp_path, {"a.py": source, "b.py": "import a\n"})

    default = analyse(tmp_path)
    filtered = analyse(tmp_path, policy=policy)

    assert check_status(default) == "fail"
    assert _cycles(filtered) == []
    assert {
        (edge.source, edge.target) for edge in filtered.architecture_dependencies
    } == {("b", "a")}


def test_forbidden_shortcut_is_caught_even_when_score_does_not_change(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        {
            "presentation.py": "import domain\n",
            "domain.py": "import storage\n",
            "storage.py": "VALUE = 1\n",
        },
    )
    rules = (("presentation", "storage"),)
    before = analyse(tmp_path, forbidden_dependencies=rules)
    (tmp_path / "presentation.py").write_text(
        "import domain\nimport storage\n", encoding="utf-8"
    )

    after = analyse(tmp_path, forbidden_dependencies=rules)

    assert before.quality.score == after.quality.score
    assert check_status(before) == "pass"
    assert check_status(after) == "fail"
    (finding,) = after.findings
    assert finding["kind"] == "forbidden_dependency"
    (edge,) = finding["witness"]
    assert (edge["source"], edge["target"]) == ("presentation", "storage")
    assert edge["evidence"][0]["line"] == 2


def test_finding_and_dependency_identities_survive_blank_lines(tmp_path: Path) -> None:
    _write(tmp_path, {"a.py": "import b\n", "b.py": "import a\n"})
    before = analyse(tmp_path, forbidden_dependencies=(("a", "b"),))
    _write(tmp_path, {"a.py": "\n\nimport b\n", "b.py": "\nimport a\n"})

    after = analyse(tmp_path, forbidden_dependencies=(("a", "b"),))

    assert [finding["id"] for finding in before.findings] == [
        finding["id"] for finding in after.findings
    ]
    assert {
        edge["id"] for finding in before.findings for edge in finding["witness"]
    } == {edge["id"] for finding in after.findings for edge in finding["witness"]}
    assert {fact.id for fact in before.import_facts}.isdisjoint(
        {fact.id for fact in after.import_facts}
    )
    assert after.findings[0]["witness"][0]["evidence"][0]["line"] == 3
