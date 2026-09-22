from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyarchgraph import cli


def _source(root: Path, *, cycle: bool = True) -> Path:
    root.mkdir()
    (root / "a.py").write_text("import b\n", encoding="utf-8")
    (root / "b.py").write_text("import a\n" if cycle else "", encoding="utf-8")
    return root


@pytest.mark.parametrize(
    ("options", "graph", "score"),
    [
        ([], True, False),
        (["--json-only"], False, False),
        (["--output", "json"], False, False),
        (["--output", "json", "--output", "graph"], True, False),
        (["--output", "json", "--output", "score"], False, True),
        (
            ["--output", "json", "--output", "graph", "--output", "score"],
            True,
            True,
        ),
    ],
)
def test_json_outputs_include_cleanup_without_changing_output_selection(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    options: list[str],
    graph: bool,
    score: bool,
) -> None:
    source = _source(tmp_path / "source")
    output = tmp_path / "out"

    assert cli.main([str(source), "--output-dir", str(output), *options]) == 0

    document = json.loads((output / "dependency-graph.json").read_text())
    cleanup = document["cleanup"]
    assert cleanup["model_version"] == "policy-debt-v1"
    assert cleanup["violation_count"] == 2
    assert cleanup["counts"] == {
        "cyclic_dependency": 2,
        "forbidden_dependency": 0,
    }
    assert cleanup["possible_violation_count"] == 0
    assert cleanup["cleanup_complete"] is False
    assert "coverage" in cleanup
    assert "work_items" in cleanup
    assert len(cleanup["violations"]) == 2
    assert "comparison" not in cleanup
    assert (output / "dependency-dag.md").is_file() is graph
    captured = capsys.readouterr()
    assert "Cleanup debt: 2 known violations" in captured.err
    assert "coverage complete" in captured.err
    assert bool(captured.out) is score
    if score:
        assert captured.out == (
            "Architecture score: 0.0/100 (experimental, architecture-v1); "
            "0 unresolved import records, 0 dynamic-import warnings\n"
        )
    else:
        assert captured.err.index("Cleanup debt:") < captured.err.index(
            "Architecture score:"
        )


@pytest.mark.parametrize("output_kind", ["score", "graph"])
def test_non_json_output_never_renders_cleanup_or_loads_a_comparator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    output_kind: str,
) -> None:
    source = _source(tmp_path / "source", cycle=False)
    output = tmp_path / "out"

    def forbidden(*args, **kwargs):
        pytest.fail("JSON and cleanup comparison must remain unselected")

    monkeypatch.setattr(cli, "render_json", forbidden)
    monkeypatch.setattr(cli, "compare_cleanup", forbidden)

    assert cli.main(
        [str(source), "--output", output_kind, "--output-dir", str(output)]
    ) == 0

    captured = capsys.readouterr()
    assert "Cleanup debt:" not in captured.err
    assert not (output / "dependency-graph.json").exists()
    if output_kind == "score":
        assert not output.exists()
        assert captured.out == (
            "Architecture score: 85.0/100 (experimental, architecture-v1); "
            "0 unresolved import records, 0 dynamic-import warnings\n"
        )
    else:
        assert captured.out == ""
        assert (output / "dependency-dag.md").is_file()


@pytest.mark.parametrize("baseline_flag", ["--baseline", "--cleanup-baseline"])
@pytest.mark.parametrize("output_kind", ["score", "graph"])
def test_baseline_modes_require_json_before_analysis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    baseline_flag: str,
    output_kind: str,
) -> None:
    source = _source(tmp_path / "source")
    output = tmp_path / "out"

    def forbidden(*args, **kwargs):
        pytest.fail("invalid output selection must fail before analysis")

    monkeypatch.setattr(cli, "analyse", forbidden)

    with pytest.raises(SystemExit) as error:
        cli.main(
            [
                str(source), "--output", output_kind, "--output-dir", str(output),
                baseline_flag, str(tmp_path / "absent.json"),
            ]
        )

    assert error.value.code == 2
    captured = capsys.readouterr()
    assert f"{baseline_flag} requires JSON output" in captured.err
    assert captured.out == ""
    assert not output.exists()


def test_baseline_flags_are_mutually_exclusive_before_analysis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = _source(tmp_path / "source")

    def forbidden(*args, **kwargs):
        pytest.fail("mutually exclusive baseline modes must fail before analysis")

    monkeypatch.setattr(cli, "analyse", forbidden)
    with pytest.raises(SystemExit) as error:
        cli.main(
            [str(source), "--baseline", "old.json", "--cleanup-baseline", "other.json"]
        )

    assert error.value.code == 2
    assert "not allowed with argument" in capsys.readouterr().err


@pytest.mark.parametrize("allow_inventory_change", [False, True])
def test_cleanup_baseline_embeds_comparison_and_forwards_inventory_choice(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    allow_inventory_change: bool,
) -> None:
    source = _source(tmp_path / "source")
    output = tmp_path / "out"
    baseline = tmp_path / "baseline.json"
    baseline.write_text('{"baseline_marker": true}\n', encoding="utf-8")
    calls = []

    def compare(current, previous, *, allow_inventory_change=False):
        calls.append((current, previous, allow_inventory_change))
        assert current["cleanup"]["violation_count"] == 2
        assert "comparison" not in current["cleanup"]
        return {"comparison_marker": True}

    monkeypatch.setattr(cli, "compare_cleanup", compare)

    def forbidden(*args, **kwargs):
        pytest.fail("cleanup comparison must not call the legacy comparator")

    monkeypatch.setattr(cli, "compare_baseline", forbidden)
    options = ["--allow-inventory-change"] if allow_inventory_change else []
    assert cli.main(
        [
            str(source), "--json-only", "--output-dir", str(output),
            "--cleanup-baseline", str(baseline), *options,
        ]
    ) == 0

    assert len(calls) == 1
    assert calls[0][1] == {"baseline_marker": True}
    assert calls[0][2] is allow_inventory_change
    document = json.loads((output / "dependency-graph.json").read_text())
    assert document["cleanup"]["comparison"] == {"comparison_marker": True}
    assert "baseline_comparison" not in document


def test_inventory_opt_in_requires_either_baseline_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = _source(tmp_path / "source")

    def forbidden(*args, **kwargs):
        pytest.fail("inventory opt-in without a baseline must fail before analysis")

    monkeypatch.setattr(cli, "analyse", forbidden)
    with pytest.raises(SystemExit) as error:
        cli.main([str(source), "--allow-inventory-change"])

    assert error.value.code == 2
    assert "requires --baseline or --cleanup-baseline" in capsys.readouterr().err


def test_cleanup_baseline_compares_published_reports(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path / "source")
    before = tmp_path / "before"
    after = tmp_path / "after"
    assert cli.main([str(source), "--json-only", "--output-dir", str(before)]) == 0
    baseline = before / "dependency-graph.json"
    (source / "b.py").write_text("", encoding="utf-8")

    assert cli.main(
        [
            str(source), "--json-only", "--output-dir", str(after),
            "--cleanup-baseline", str(baseline), "--check",
        ]
    ) == 0

    document = json.loads((after / "dependency-graph.json").read_text())
    cleanup = document["cleanup"]
    comparison = cleanup["comparison"]
    assert cleanup["violation_count"] == 0
    assert cleanup["cleanup_complete"] is True
    assert comparison["compatible"] is True
    assert comparison["before_violation_count"] == 2
    assert comparison["current_violation_count"] == 0
    assert comparison["count_delta"] == -2
    assert len(comparison["verified_resolved"]) == 2
    assert comparison["has_new_violations"] is False
    assert comparison["needs_review"] is False
    assert "baseline_comparison" not in document


def test_cleanup_inventory_change_requires_opt_in_and_remains_visible(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path / "source")
    before = tmp_path / "before"
    after = tmp_path / "after"
    assert cli.main([str(source), "--json-only", "--output-dir", str(before)]) == 0
    baseline = before / "dependency-graph.json"
    (source / "unrelated.py").write_text("", encoding="utf-8")
    options = [
        str(source), "--json-only", "--output-dir", str(after),
        "--cleanup-baseline", str(baseline),
    ]

    assert cli.main(options) == 2
    assert not after.exists()
    assert cli.main([*options, "--allow-inventory-change"]) == 0

    document = json.loads((after / "dependency-graph.json").read_text())
    comparison = document["cleanup"]["comparison"]
    assert comparison["added_modules"] == [["unrelated", "unrelated.py"]]
    assert comparison["count_delta"] == 0
    assert comparison["needs_review"] is True


@pytest.mark.parametrize("baseline_contents", ["[]", "{}", "not JSON"])
def test_incompatible_cleanup_baseline_preserves_artifacts_and_suppresses_stdout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    baseline_contents: str,
) -> None:
    source = _source(tmp_path / "source")
    output = tmp_path / "out"
    output.mkdir()
    json_path = output / "dependency-graph.json"
    markdown_path = output / "dependency-dag.md"
    json_path.write_text("previous JSON\n", encoding="utf-8")
    markdown_path.write_text("previous Markdown\n", encoding="utf-8")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(baseline_contents, encoding="utf-8")

    assert cli.main(
        [
            str(source), "--output", "json", "--output", "graph", "--output", "score",
            "--output-dir", str(output), "--cleanup-baseline", str(baseline),
        ]
    ) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Cleanup debt:" not in captured.err
    assert json_path.read_text() == "previous JSON\n"
    assert markdown_path.read_text() == "previous Markdown\n"
    assert not tuple(output.glob(".*.tmp"))


def test_partial_analysis_publishes_cleanup_with_existing_failure_exit(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = _source(tmp_path / "source")
    (source / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    output = tmp_path / "out"

    assert cli.main([str(source), "--json-only", "--output-dir", str(output)]) == 1

    document = json.loads((output / "dependency-graph.json").read_text())
    assert document["analysis"]["complete"] is False
    assert document["quality"]["score"] is None
    assert document["cleanup"]["violation_count"] == 2
    assert document["cleanup"]["cleanup_complete"] is False
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Cleanup debt: 2 known violations" in captured.err
    assert "coverage needs review" in captured.err


def test_partial_cleanup_comparison_publishes_review_details(
    tmp_path: Path,
) -> None:
    source = _source(tmp_path / "source")
    before = tmp_path / "before"
    after = tmp_path / "after"
    assert cli.main([str(source), "--json-only", "--output-dir", str(before)]) == 0
    baseline = before / "dependency-graph.json"
    (source / "b.py").write_text("def broken(:\n", encoding="utf-8")

    assert cli.main(
        [
            str(source), "--json-only", "--output-dir", str(after),
            "--cleanup-baseline", str(baseline),
        ]
    ) == 1

    document = json.loads((after / "dependency-graph.json").read_text())
    comparison = document["cleanup"]["comparison"]
    assert document["cleanup"]["cleanup_complete"] is False
    assert comparison["compatible"] is True
    assert comparison["needs_review"] is True
    assert comparison["verified_resolved"] == []
    assert len(comparison["disappeared_unverified"]) == 2


def test_publication_failure_does_not_emit_cleanup_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = _source(tmp_path / "source")

    def fail_publication(*args, **kwargs):
        raise OSError("injected publication failure")

    monkeypatch.setattr(cli, "_write_outputs_atomically", fail_publication)
    assert cli.main(
        [str(source), "--json-only", "--output-dir", str(tmp_path / "out")]
    ) == 2

    captured = capsys.readouterr()
    assert "injected publication failure" in captured.err
    assert "Cleanup debt:" not in captured.err
    assert captured.out == ""
