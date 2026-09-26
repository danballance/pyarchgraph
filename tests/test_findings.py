"""The public report describes actionable violations with bounded evidence."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyarchgraph import analyse


def _write(root: Path, files: dict[str, str]) -> None:
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")


def _cycles(report):
    return [finding for finding in report.findings if finding.kind == "cycle"]


def _assert_closed_witness(finding) -> None:
    witness = finding.witness
    assert 1 <= len(witness) <= len(finding.members)
    assert len({edge.source for edge in witness}) == len(witness)
    for index, edge in enumerate(witness):
        assert edge.source in finding.members
        assert edge.target == witness[(index + 1) % len(witness)].source
        assert edge.evidence
        for evidence in edge.evidence:
            assert evidence.path
            assert evidence.line >= 1
            assert evidence.column >= 1
            assert evidence.source_segment


def test_definite_cycle_has_one_closed_witness_with_import_locations(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write(tmp_path, {"a.py": "# consumer\nimport b\n", "b.py": "import a\n"})
    report = analyse(tmp_path)
    (cycle,) = _cycles(report)
    assert cycle.certainty == "definite"
    assert cycle.members == cycle.definite_members == ("a", "b")
    assert report.module_count == report.dependency_count == 2
    _assert_closed_witness(cycle)
    assert {
        (edge.source, evidence.path, evidence.line, evidence.column)
        for edge in cycle.witness
        for evidence in edge.evidence
    } == {("a", "a.py", 2, 1), ("b", "b.py", 1, 1)}


def test_shadowable_child_cycle_is_possible(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "pkg/__init__.py": "b = 42\n",
            "pkg/a.py": "from pkg import b\n",
            "pkg/b.py": "import pkg.a\n",
        },
    )
    (cycle,) = _cycles(analyse(tmp_path))
    assert cycle.certainty == "possible"
    assert cycle.definite_members == ()
    _assert_closed_witness(cycle)
    assert any(
        e.resolution_kind.value == "probable_submodule"
        for edge in cycle.witness
        for e in edge.evidence
    )


def test_mixed_component_prefers_definite_witness(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/a.py": "import pkg.b\n",
            "pkg/b.py": "import pkg.a\nfrom . import c\n",
            "pkg/c.py": "import pkg.b\n",
        },
    )
    (cycle,) = _cycles(analyse(tmp_path))
    assert cycle.members == ("pkg.a", "pkg.b", "pkg.c")
    assert cycle.definite_members == ("pkg.a", "pkg.b")
    assert cycle.certainty == "definite"
    _assert_closed_witness(cycle)
    assert {edge.source for edge in cycle.witness} == {"pkg.a", "pkg.b"}
    assert all(
        e.resolution_kind.value in {"exact_module", "exact_base"}
        for edge in cycle.witness
        for e in edge.evidence
    )


def test_bow_tie_witness_does_not_claim_to_visit_entire_component(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        {"a.py": "import b\n", "b.py": "import a\nimport c\n", "c.py": "import b\n"},
    )
    (cycle,) = _cycles(analyse(tmp_path))
    assert cycle.members == ("a", "b", "c")
    assert len(cycle.witness) == 2
    _assert_closed_witness(cycle)


def test_dense_components_each_have_one_bounded_witness(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            f"{name}.py": "".join(
                f"import {other}\n" for other in group if other != name
            )
            for group in ("abcdef", "xyz")
            for name in group
        },
    )
    cycles = _cycles(analyse(tmp_path))
    assert [c.members for c in cycles] == [tuple("abcdef"), tuple("xyz")]
    for cycle in cycles:
        _assert_closed_witness(cycle)


def test_self_import_has_one_edge_witness(tmp_path: Path) -> None:
    _write(tmp_path, {"a.py": "import a\n"})
    (cycle,) = _cycles(analyse(tmp_path))
    assert cycle.members == cycle.definite_members == ("a",)
    assert len(cycle.witness) == 1
    _assert_closed_witness(cycle)


@pytest.mark.parametrize(
    "source",
    [
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import b\n",
        "def late():\n    import b\n",
        "class Container:\n    import b\n",
        "if False:\n    import b\n",
        "def late():\n    return\n    import b\n",
    ],
)
def test_local_and_typing_only_cycles_always_block(tmp_path: Path, source: str) -> None:
    _write(tmp_path, {"a.py": source, "b.py": "import a\n"})
    report = analyse(tmp_path)
    (cycle,) = _cycles(report)
    assert cycle.certainty == "definite"
    assert report.dependency_count == 2
    _assert_closed_witness(cycle)


def test_duplicate_import_sites_preserved_without_counting_extra_edges(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        {
            "a.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import b\ndef late():\n    import b\nimport b\n",
            "b.py": "import a\n",
        },
    )
    report = analyse(tmp_path)
    assert report.dependency_count == 2
    (cycle,) = _cycles(report)
    edge = next(edge for edge in cycle.witness if edge.source == "a")
    assert {e.line for e in edge.evidence} == {3, 5, 6}


def test_acyclic_probable_dependency_passes(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "pkg/__init__.py": "b = 42\n",
            "pkg/a.py": "from pkg import b\n",
            "pkg/b.py": "",
        },
    )
    report = analyse(tmp_path)
    assert report.dependency_count == 1
    assert report.findings == ()


def test_exact_support_makes_possible_cycle_definite(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/a.py": "from pkg import b\n",
            "pkg/b.py": "import pkg.a\n",
        },
    )
    before = analyse(tmp_path)
    (possible,) = before.findings
    assert possible.certainty == "possible"
    assert possible.definite_members == ()

    (tmp_path / "pkg/a.py").write_text("from pkg import b\nimport pkg.b\n")
    after = analyse(tmp_path)
    (definite,) = after.findings
    assert before.dependency_count == after.dependency_count == 2
    assert definite.certainty == "definite"
    assert definite.members == definite.definite_members == possible.members
    edge = next(edge for edge in definite.witness if edge.source == "pkg.a")
    assert [(e.line, e.resolution_kind.value) for e in edge.evidence] == [
        (2, "exact_module"),
    ]
    _assert_closed_witness(possible)
    _assert_closed_witness(definite)


def test_blank_lines_preserve_semantic_findings_and_update_locations(
    tmp_path: Path,
) -> None:
    _write(tmp_path, {"a.py": "import b\n", "b.py": "import a\n"})
    before = analyse(tmp_path)
    _write(tmp_path, {"a.py": "\n\nimport b\n", "b.py": "\nimport a\n"})
    after = analyse(tmp_path)
    assert [
        (f.kind, f.certainty, [(e.source, e.target) for e in f.witness])
        for f in before.findings
    ] == [
        (f.kind, f.certainty, [(e.source, e.target) for e in f.witness])
        for f in after.findings
    ]
    assert {
        e.line
        for f in after.findings
        for edge in f.witness
        if edge.source == "a"
        for e in edge.evidence
    } == {3}
