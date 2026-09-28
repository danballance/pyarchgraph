"""Process exit codes belong to the CLI presentation boundary."""

import pytest

from pyarchgraph.adapters.driving.cli.application import CliExitCodePolicy
from pyarchgraph.application.results import (
    AnalysisReport,
    Coverage,
    RegisteredFinding,
    ViewReport,
)
from pyarchgraph.domain.models import RuleFinding, Severity


def _view(severity: Severity | None) -> ViewReport:
    findings = (
        (RegisteredFinding("review", severity, RuleFinding("review", "Review.")),)
        if severity is not None
        else ()
    )
    return ViewReport((), ("review",), 0, 0, 0, findings)


@pytest.mark.parametrize(
    "status,severity,expected",
    [
        ("complete", None, 0),
        ("complete", Severity.INFO, 0),
        ("complete", Severity.WARNING, 0),
        ("complete", Severity.ERROR, 1),
        ("incomplete", None, 2),
        ("incomplete", Severity.WARNING, 2),
        ("incomplete", Severity.ERROR, 2),
    ],
)
def test_exit_policy_prioritizes_coverage_and_only_selected_errors(
    status, severity, expected
):
    report = AnalysisReport(
        status=status,
        gate="selected",
        sources=(),
        coverage=Coverage((), (), (), 0, (), ()),
        views={"selected": _view(severity), "other": _view(Severity.ERROR)},
    )
    assert not hasattr(report, "exit_code")
    assert CliExitCodePolicy().exit_code(report) == expected
