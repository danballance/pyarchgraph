"""Graph selection never suppresses uncertainty in the raw source observations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyarchgraph import analyse
from pyarchgraph.cli import main
from pyarchgraph.findings import check_status
from pyarchgraph.model import ImportScope, ImportSyntax, ResolutionKind
from pyarchgraph.policy import GraphPolicy


def _write_fixture(root: Path, scope: str, observation: str) -> None:
    package = root / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "b.py").write_text("", encoding="utf-8")
    guard = "if TYPE_CHECKING:" if scope == "typing" else "def later():"
    statement = {
        "missing": "import pkg.missing",
        "dynamic_literal": "import_module('pkg.b')",
        "dynamic_expression": "import_module(target)",
    }[observation]
    source = (
        "from typing import TYPE_CHECKING\n"
        "from importlib import import_module\n"
        f"{guard}\n"
        "    import pkg.b\n"
        f"    {statement}\n"
    )
    (package / "a.py").write_text(source, encoding="utf-8")


@pytest.mark.parametrize("scope", ["typing", "local"])
@pytest.mark.parametrize(
    "observation", ["missing", "dynamic_literal", "dynamic_expression"]
)
def test_api_graph_filters_preserve_raw_uncertainty(
    tmp_path: Path,
    scope: str,
    observation: str,
) -> None:
    _write_fixture(tmp_path, scope, observation)
    policy = GraphPolicy(
        include_type_only=scope != "typing", include_local=scope != "local"
    )

    raw = analyse(tmp_path)
    selected = analyse(tmp_path, policy=policy)

    assert {(edge.source, edge.target) for edge in raw.architecture_dependencies} == {
        ("pkg.a", "pkg.b"),
    }
    assert selected.architecture_dependencies == ()
    assert selected.findings == ()
    assert selected.import_facts == raw.import_facts
    assert selected.dependencies == raw.dependencies
    assert selected.unresolved_imports == raw.unresolved_imports
    assert selected.diagnostics == raw.diagnostics
    assert selected.complete is True
    assert selected.dependency_resolution_complete is False
    assert check_status(selected) == "needs_review"
    target_facts = [
        fact
        for fact in selected.import_facts
        if fact.base_module and fact.base_module.startswith("pkg.")
    ]
    assert target_facts
    assert all(fact.type_only == (scope == "typing") for fact in target_facts)
    assert all(
        fact.scope is (ImportScope.MODULE if scope == "typing" else ImportScope.LOCAL)
        for fact in target_facts
    )
    if observation == "missing":
        (unresolved,) = selected.unresolved_imports
        assert unresolved.requested == "pkg.missing"
        assert unresolved.reason.value == "missing_internal_target"
        assert selected.quality.unresolved_import_count == 1
    else:
        assert selected.quality.dynamic_import_warning_count == 1
        assert [diagnostic.code for diagnostic in selected.diagnostics] == [
            "dynamic_import_ignored",
        ]
        if observation == "dynamic_literal":
            assert any(
                fact.syntax is ImportSyntax.DYNAMIC_IMPORT for fact in target_facts
            )
            assert any(
                item.resolution_kind is ResolutionKind.DYNAMIC_LITERAL
                for edge in selected.dependencies
                for item in edge.evidence
            )


@pytest.mark.parametrize("scope", ["typing", "local"])
@pytest.mark.parametrize(
    "observation", ["missing", "dynamic_literal", "dynamic_expression"]
)
def test_cli_graph_filters_still_require_review_for_raw_uncertainty(
    tmp_path: Path,
    scope: str,
    observation: str,
) -> None:
    source = tmp_path / "source"
    output = tmp_path / "output"
    _write_fixture(source, scope, observation)
    flag = "--exclude-type-only" if scope == "typing" else "--exclude-local"

    status = main(
        [str(source), "--json-only", "--check", flag, "--output-dir", str(output)]
    )

    assert status == 4
    document = json.loads((output / "dependency-graph.json").read_text())
    assert document["architecture_dependencies"] == []
    assert document["dependencies"]
    assert document["findings"] == []
    assert document["analysis"]["complete"] is True
    assert document["analysis"]["dependency_resolution_complete"] is False
    assert document["check"]["status"] == "needs_review"
    if observation == "missing":
        assert [item["requested"] for item in document["unresolved_imports"]] == [
            "pkg.missing",
        ]
    else:
        assert [item["code"] for item in document["diagnostics"]] == [
            "dynamic_import_ignored",
        ]
