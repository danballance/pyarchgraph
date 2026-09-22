from __future__ import annotations

from itertools import combinations
import json
from pathlib import Path
import sys

import pytest

from pyarchgraph import cli, graph_ops, rendering


FILENAMES = {"json": "dependency-graph.json", "graph": "dependency-dag.md"}
OUTPUT_COMBINATIONS = [
    selection
    for count in range(1, 4)
    for selection in combinations(("json", "graph", "score"), count)
]
SCORE = (
    "Architecture score: 85.0/100 (experimental, architecture-v1); "
    "0 unresolved import records, 0 dynamic-import warnings\n"
)


def _write(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _options(selection: tuple[str, ...]) -> list[str]:
    return [argument for output in selection for argument in ("--output", output)]


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "src"
    _write(root, "a.py", "import b\n")
    _write(root, "b.py", "VALUE = 1\n")
    return root


@pytest.mark.parametrize("selection", OUTPUT_COMBINATIONS)
def test_selected_outputs_and_streams(
    source: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    selection: tuple[str, ...],
) -> None:
    output = tmp_path / "out"

    assert cli.main([str(source), "--output-dir", str(output), *_options(selection)]) == 0

    captured = capsys.readouterr()
    assert captured.out == (SCORE if "score" in selection else "")
    assert "2 modules, 1 raw edges, 0 cyclic components, 0 diagnostics" in captured.err
    assert "pyarchgraph: policy check: pass" in captured.err
    assert ("Architecture score:" in captured.err) == ("score" not in selection)
    expected_files = {FILENAMES[item] for item in selection if item in FILENAMES}
    actual_files = {path.name for path in output.iterdir()} if output.exists() else set()
    assert actual_files == expected_files
    if "json" in selection:
        document = json.loads((output / FILENAMES["json"]).read_text())
        assert document["quality"]["score"] == 85.0
        assert document["check"]["status"] == "pass"
        assert len(document["dag"]["edges"]) == 1
    if "graph" in selection:
        markdown = (output / FILENAMES["graph"]).read_text()
        assert "Architecture score: 85.0/100" in markdown
        assert "flowchart TD" in markdown
    if not expected_files:
        assert not output.exists()
        assert "wrote" not in captured.err


@pytest.mark.parametrize("legacy_flags", [[], ["--json-only"]])
def test_defaults_and_json_only_keep_legacy_artifacts_and_streams(
    source: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    legacy_flags: list[str],
) -> None:
    legacy_output = tmp_path / "legacy"
    selected_output = tmp_path / "selected"
    selection = ("json",) if legacy_flags else ("json", "graph")

    assert cli.main([str(source), "--output-dir", str(legacy_output), *legacy_flags]) == 0
    legacy_streams = capsys.readouterr()
    assert legacy_streams.out == ""
    assert f"pyarchgraph: {SCORE}" in legacy_streams.err
    assert cli.main([
        str(source), "--output-dir", str(selected_output), *_options(selection)
    ]) == 0
    assert sorted(path.name for path in legacy_output.iterdir()) == sorted(
        FILENAMES[item] for item in selection
    )
    for item in selection:
        name = FILENAMES[item]
        assert (legacy_output / name).read_bytes() == (selected_output / name).read_bytes()


def test_duplicate_selectors_render_and_print_once(
    source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls = {"json": 0, "graph": 0}

    def counted(name, renderer):
        def render(*args, **kwargs):
            calls[name] += 1
            return renderer(*args, **kwargs)

        return render

    monkeypatch.setattr(cli, "render_json", counted("json", cli.render_json))
    monkeypatch.setattr(
        cli, "render_mermaid_markdown", counted("graph", cli.render_mermaid_markdown)
    )
    assert cli.main([
        str(source), "--output-dir", str(tmp_path / "out"),
        *_options(("score", "json", "graph", "json", "score", "graph")),
    ]) == 0

    assert calls == {"json": 1, "graph": 1}
    assert capsys.readouterr().out == SCORE


@pytest.mark.parametrize(
    "flags",
    [
        ["--output", "invalid"],
        ["--output"],
        ["--json-only", "--output", "json"],
        ["--output", "score", "--json-only"],
    ],
)
def test_invalid_selectors_fail_before_analysis(
    source: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, flags: list[str]
) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid output options reached analysis")

    monkeypatch.setattr(cli, "analyse", forbidden)
    output = tmp_path / "out"
    with pytest.raises(SystemExit) as caught:
        cli.main([str(source), "--output-dir", str(output), *flags])
    assert caught.value.code == 2
    assert not output.exists()


@pytest.mark.parametrize(
    "selection",
    [selection for selection in OUTPUT_COMBINATIONS
     if not {"json", "graph"}.issubset(selection)],
)
def test_unselected_file_renderers_and_diagram_work_are_skipped(
    source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selection: tuple[str, ...],
) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("An unselected output performed rendering work")

    if "json" not in selection:
        monkeypatch.setattr(cli, "render_json", forbidden)
    if "graph" not in selection:
        monkeypatch.setattr(cli, "render_mermaid_markdown", forbidden)
        monkeypatch.setattr(rendering, "essential_edges", forbidden)
        monkeypatch.setattr(graph_ops.nx, "transitive_reduction", forbidden)

    assert cli.main([
        str(source), "--output-dir", str(tmp_path / "out"), *_options(selection)
    ]) == 0


@pytest.mark.parametrize("existing_output", [False, True])
def test_score_only_never_validates_or_creates_the_output_directory(
    source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing_output: bool,
) -> None:
    output = tmp_path / "not-a-directory"
    if existing_output:
        output.write_text("existing unrelated file", encoding="utf-8")

    def guard(operation):
        def guarded(path, *args, **kwargs):
            if path == output or output in path.parents:
                pytest.fail("Score-only touched an output destination")
            return operation(path, *args, **kwargs)

        return guarded

    for operation in ("exists", "is_dir", "is_file", "mkdir"):
        monkeypatch.setattr(Path, operation, guard(getattr(Path, operation)))

    assert cli.main([
        str(source), "--output-dir", str(output), "--output", "score"
    ]) == 0
    if existing_output:
        assert output.read_text() == "existing unrelated file"
    else:
        assert output not in tuple(tmp_path.iterdir())


@pytest.mark.parametrize("selection", [("json",), ("graph",), ("score",)])
@pytest.mark.parametrize("unselected_is_directory", [False, True])
def test_unselected_destinations_are_left_untouched(
    source: Path,
    tmp_path: Path,
    selection: tuple[str, ...],
    unselected_is_directory: bool,
) -> None:
    output = tmp_path / "out"
    output.mkdir()
    untouched = []
    for kind, filename in FILENAMES.items():
        if kind not in selection:
            destination = output / filename
            if unselected_is_directory:
                destination.mkdir()
            else:
                destination.write_text("previous artifact", encoding="utf-8")
            untouched.append(destination)

    assert cli.main([str(source), "--output-dir", str(output), *_options(selection)]) == 0
    for destination in untouched:
        if unselected_is_directory:
            assert destination.is_dir()
            assert list(destination.iterdir()) == []
        else:
            assert destination.read_text() == "previous artifact"


@pytest.mark.parametrize("selection", ["json", "graph"])
def test_selected_non_file_targets_are_rejected_before_analysis(
    source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    selection: str,
) -> None:
    output = tmp_path / "out"
    (output / FILENAMES[selection]).mkdir(parents=True)

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid selected target reached analysis")

    monkeypatch.setattr(cli, "analyse", forbidden)
    with pytest.raises(SystemExit) as caught:
        cli.main([str(source), "--output-dir", str(output), "--output", selection])
    assert caught.value.code == 2


@pytest.mark.parametrize("selection", [("graph",), ("score",), ("graph", "score")])
def test_baseline_without_json_is_rejected_before_analysis(
    source: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    selection: tuple[str, ...],
) -> None:
    def forbidden(*args, **kwargs):
        pytest.fail("Baseline without JSON reached analysis")

    monkeypatch.setattr(cli, "analyse", forbidden)
    output = tmp_path / "out"
    with pytest.raises(SystemExit) as caught:
        cli.main([
            str(source), "--output-dir", str(output), *_options(selection),
            "--baseline", str(tmp_path / "baseline.json"),
        ])
    assert caught.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--baseline" in captured.err and "json" in captured.err.lower()
    assert not output.exists()


@pytest.mark.parametrize(
    "flags", [[], ["--json-only"], _options(("json", "score"))]
)
def test_baseline_comparison_remains_available_with_json(
    source: Path, tmp_path: Path, flags: list[str]
) -> None:
    before = tmp_path / "before"
    after = tmp_path / "after"
    assert cli.main([str(source), "--json-only", "--output-dir", str(before)]) == 0
    assert cli.main([
        str(source), "--output-dir", str(after), *flags,
        "--baseline", str(before / FILENAMES["json"]),
    ]) == 0

    document = json.loads((after / FILENAMES["json"]).read_text())
    assert document["baseline_comparison"]["compatible"] is True
    assert document["baseline_comparison"]["added_dependencies"] == []


@pytest.mark.parametrize(
    ("source_files", "flags", "reason", "status", "diagnostic"),
    [
        ({}, [], "no modules", 0, None),
        ({"broken.py": "def invalid(:\n"}, [], "incomplete analysis", 1,
         "source_syntax_error"),
        ({"present.py": ""}, ["--expect-package", "absent"], "invalid scope", 0,
         "expected_package_missing"),
    ],
)
def test_score_only_explains_unavailable_scores_and_preserves_diagnostics(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    source_files: dict[str, str],
    flags: list[str],
    reason: str,
    status: int,
    diagnostic: str | None,
) -> None:
    source = tmp_path / "src"
    source.mkdir()
    for relative, content in source_files.items():
        _write(source, relative, content)
    output = tmp_path / "out"

    assert cli.main([
        str(source), "--output-dir", str(output), "--output", "score", *flags
    ]) == status

    captured = capsys.readouterr()
    assert captured.out.startswith(
        f"Architecture score: unavailable ({reason}) (experimental, architecture-v1); "
    )
    assert "0 unresolved import records, 0 dynamic-import warnings\n" in captured.out
    assert "Architecture score:" not in captured.err
    assert "policy check: needs_review" in captured.err
    if diagnostic is not None:
        assert diagnostic in captured.err
        assert diagnostic not in captured.out
    assert not output.exists()


@pytest.mark.parametrize("selection", [("json",), ("graph",), ("score",)])
@pytest.mark.parametrize(
    ("source_files", "status", "policy"),
    [
        ({"a.py": "import b\n", "b.py": ""}, 0, "pass"),
        ({"a.py": "import b\n", "b.py": "import a\n"}, 3, "fail"),
        ({"pkg/__init__.py": "b = 42\n", "pkg/a.py": "from pkg import b\n",
          "pkg/b.py": "import pkg.a\n"}, 4, "needs_review"),
        ({"broken.py": "def invalid(:\n"}, 1, "needs_review"),
    ],
)
def test_output_selection_preserves_check_statuses(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    selection: tuple[str, ...],
    source_files: dict[str, str],
    status: int,
    policy: str,
) -> None:
    source = tmp_path / "src"
    for relative, content in source_files.items():
        _write(source, relative, content)

    assert cli.main([
        str(source), "--output-dir", str(tmp_path / "out"),
        "--check", *_options(selection),
    ]) == status
    captured = capsys.readouterr()
    assert f"pyarchgraph: policy check: {policy}" in captured.err
    assert "policy check:" not in captured.out


def test_score_stdout_keeps_import_limitations_and_diagnostics_separate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "src"
    _write(
        source, "pkg/a.py",
        "import pkg.b\nimport pkg.missing\nimport importlib\n"
        "importlib.import_module('pkg.dynamic')\n",
    )
    _write(source, "pkg/b.py", "")

    assert cli.main([
        str(source), "--output-dir", str(tmp_path / "out"), "--output", "score"
    ]) == 0
    captured = capsys.readouterr()
    assert captured.out == SCORE.replace(
        "0 unresolved import records, 0 dynamic-import warnings",
        "2 unresolved import records, 1 dynamic-import warnings",
    )
    assert "dynamic_import_ignored" in captured.err
    assert "Architecture score:" not in captured.err


@pytest.mark.skipif(sys.version_info < (3, 12), reason="PEP 695 syntax")
def test_type_alias_evidence_gives_the_same_score_in_independent_outputs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "src"
    output = tmp_path / "out"
    _write(
        source, "a.py",
        "from importlib import import_module\n"
        "type Alias = import_module('b').Value\n",
    )
    _write(source, "b.py", "class Value:\n    pass\n")

    assert cli.main([
        str(source), "--output-dir", str(output), *_options(("json", "score"))
    ]) == 0
    combined = capsys.readouterr()
    document = json.loads((output / FILENAMES["json"]).read_text())
    assert document["analysis"]["complete"] is True
    assert document["quality"]["score"] is not None
    assert document["quality"]["dynamic_import_warning_count"] == 1
    assert any(fact["syntax"] == "dynamic_import" for fact in document["import_facts"])
    assert "1 dynamic-import warnings" in combined.out

    score_output = tmp_path / "score-only"
    assert cli.main([
        str(source), "--output-dir", str(score_output), "--output", "score"
    ]) == 0
    assert capsys.readouterr().out == combined.out
    assert not score_output.exists()


@pytest.mark.parametrize("implied_edges", ["dotted", "solid", "omit"])
def test_graph_only_preserves_package_projection_and_edge_presentation(
    tmp_path: Path, implied_edges: str
) -> None:
    source = tmp_path / "src"
    output = tmp_path / "out"
    _write(source, "api/entry.py", "import domain.logic\nimport storage.db\n")
    _write(source, "domain/logic.py", "import storage.db\n")
    _write(source, "storage/db.py", "")

    assert cli.main([
        str(source), "--output-dir", str(output), "--output", "graph",
        "--view", "package", "--package-depth", "1", "--implied-edges", implied_edges,
    ]) == 0
    markdown = (output / FILENAMES["graph"]).read_text()
    assert '["api"]' in markdown
    assert '["domain"]' in markdown
    assert '["storage"]' in markdown
    diagram = markdown.split("```mermaid\n", 1)[1]
    assert "api.entry" not in diagram
    assert "domain.logic" not in diagram
    assert "1 of 3 edges are implied" in markdown
    assert ("-.->" in diagram) == (implied_edges == "dotted")
    if implied_edges == "omit":
        assert "--output json" in markdown
        assert "are not drawn" in markdown
    assert not (output / FILENAMES["json"]).exists()
