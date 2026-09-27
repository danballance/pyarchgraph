"""Consumer extensions use the public API without adding engine dispatch."""

import json
from pathlib import Path

import pytest

from examples.custom_strategies import (
    ExampleApplication,
    GroupSizeCheck,
    PackageGroupingView,
)
from pyarchgraph import (
    AnalysisOptions,
    ApplicationFactory,
    CheckRegistration,
    CheckResult,
    JsonReportRenderer,
    RuleFinding,
    Severity,
    ViewRegistration,
)


def test_external_grouping_and_advisory_check_need_no_core_changes(tmp_path):
    for package in ("one", "two"):
        (tmp_path / package).mkdir()
        (tmp_path / package / "__init__.py").write_text("")
        (tmp_path / package / "leaf.py").write_text("")
    (tmp_path / "one" / "a.py").write_text("import two.leaf\nimport one.leaf")
    (tmp_path / "two" / "b.py").write_text("import one.leaf")
    # These module edges are acyclic, but form a cycle after package projection.
    factory = ApplicationFactory(
        views=ApplicationFactory.default_views()
        + (ViewRegistration("packages", PackageGroupingView()),),
        checks=ApplicationFactory.default_checks()
        + (
            CheckRegistration(
                "group-size", GroupSizeCheck(maximum_sources=1), ("packages",)
            ),
        ),
        check_selection={"packages": ("group-size",), "structural": ()},
    )
    report = factory.create_analyzer().analyse(
        (Path("."),), options=AnalysisOptions(gate="packages"), base_dir=tmp_path
    )
    assert report.status == "complete" and report.exit_code == 0
    assert report.views["structural"].cyclic_node_count == 0
    assert report.selected_view.cyclic_node_count == 2
    assert report.selected_view.dependency_count == 2
    assert report.selected_view.enabled_check_ids == ("group-size",)
    rendered = json.loads(JsonReportRenderer().render(report))
    envelopes = rendered["views"]["packages"]["findings"]
    assert len(envelopes) == 2
    assert all(
        item["check_id"] == "group-size"
        and item["severity"] == "warning"
        and item["finding"]["kind"] == "rule"
        for item in envelopes
    )


def test_custom_composition_cli_exposes_custom_gate(tmp_path, capsys):
    (tmp_path / "app.py").write_text("")
    assert (
        ExampleApplication()
        .create_factory()
        .create_cli()
        .run([str(tmp_path), "--gate", "packages"])
        == 0
    )
    document = json.loads(capsys.readouterr().out)
    assert document["gate"] == "packages"
    assert document["views"]["non-typing"]["enabled_check_ids"] == []


@pytest.mark.parametrize(
    "severity,exit_code",
    [(Severity.INFO, 0), (Severity.WARNING, 0), (Severity.ERROR, 1)],
)
def test_selected_gate_uses_finding_severity(tmp_path, severity, exit_code):
    class CustomCheck:
        def evaluate(self, context):
            return (
                CheckResult(severity, RuleFinding("review", "Review this project.")),
            )

    (tmp_path / "app.py").write_text("")
    analyzer = ApplicationFactory(
        checks=(CheckRegistration("review", CustomCheck()),),
        check_selection={"non-typing": ()},
    ).create_analyzer()
    report = analyzer.analyse(
        (Path("."),), options=AnalysisOptions(), base_dir=tmp_path
    )
    assert report.status == "complete" and report.exit_code == exit_code
    unselected = analyzer.analyse(
        (Path("."),), options=AnalysisOptions(gate="non-typing"), base_dir=tmp_path
    )
    assert unselected.views["structural"].findings
    assert unselected.exit_code == 0


@pytest.mark.parametrize("checks_enabled", [False, True])
def test_incomplete_coverage_takes_precedence_over_check_selection(
    tmp_path, checks_enabled
):
    (tmp_path / "a.py").write_text("import b\n")
    (tmp_path / "b.py").write_text("import a\n")
    (tmp_path / "broken.py").write_text("def broken(:\n")
    factory = ApplicationFactory(
        checks=ApplicationFactory.default_checks() if checks_enabled else ()
    )
    report = factory.create_analyzer().analyse(
        (Path("."),), options=AnalysisOptions(), base_dir=tmp_path
    )
    assert report.status == "incomplete" and report.exit_code == 2
    assert bool(report.selected_view.findings) is checks_enabled
    assert report.selected_view.cyclic_node_count == 2
    assert any(
        item.code == "source_syntax_error" for item in report.coverage.diagnostics
    )


@pytest.mark.parametrize("extension_family", ["view", "check"])
def test_cli_extension_failure_has_context_and_no_report(
    tmp_path, capsys, extension_family
):
    class FailingView:
        def transform(self, snapshot):
            raise RuntimeError("deliberate failure")

    class FailingCheck:
        def evaluate(self, context):
            raise RuntimeError("deliberate failure")

    (tmp_path / "app.py").write_text("")
    factory = (
        ApplicationFactory(views=(ViewRegistration("custom", FailingView()),))
        if extension_family == "view"
        else ApplicationFactory(checks=(CheckRegistration("custom", FailingCheck()),))
    )
    gate = "custom" if extension_family == "view" else "structural"
    assert factory.create_cli().run([str(tmp_path), "--gate", gate]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert f"{extension_family} extension 'custom'" in output.err
    assert f"view '{gate}'" in output.err
    assert "deliberate failure" in output.err
