"""Registration policy has no graph backend dependency."""

import pytest

from pyarchgraph.application.exceptions import AnalysisError
from pyarchgraph.application.strategies import (
    CheckRegistration,
    StrategyRegistry,
    ViewRegistration,
)
from pyarchgraph.domain.models import CheckResult, RuleFinding, Severity
from pyarchgraph.domain.strategies import (
    CheckStrategy,
    CycleCheck,
    NonTypingView,
    StructuralView,
    UnresolvedImportCheck,
)


class AdvisoryCheck(CheckStrategy):
    def evaluate(self, context):
        return (
            CheckResult(Severity.WARNING, RuleFinding("advice", "Review this graph.")),
        )


def _registry(*, views=None, checks=None, selection=None):
    return StrategyRegistry(
        views
        if views is not None
        else (ViewRegistration("structural", StructuralView()),),
        checks
        if checks is not None
        else (
            CheckRegistration("cycles", CycleCheck()),
            CheckRegistration("unresolved-imports", UnresolvedImportCheck()),
        ),
        selection or {},
    )


@pytest.mark.parametrize(
    "arguments, message",
    [
        (
            {
                "views": (
                    ViewRegistration("same", StructuralView()),
                    ViewRegistration("same", StructuralView()),
                )
            },
            "duplicate",
        ),
        ({"views": (ViewRegistration("structural", NonTypingView()),)}, "reserved"),
        ({"checks": (CheckRegistration("cycles", AdvisoryCheck()),)}, "reserved"),
        ({"checks": (CheckRegistration("_hidden", AdvisoryCheck()),)}, "extension IDs"),
        (
            {"checks": (CheckRegistration("advice", AdvisoryCheck(), ("missing",)),)},
            "unknown views",
        ),
        ({"selection": {"missing": ()}}, "unknown view"),
        ({"selection": {"structural": ("missing",)}}, "unknown checks"),
        ({"selection": {"structural": ("cycles", "cycles")}}, "duplicate"),
    ],
)
def test_registry_rejects_invalid_configuration(arguments, message):
    with pytest.raises(AnalysisError, match=message):
        _registry(**arguments)


def test_unknown_gate_is_rejected():
    with pytest.raises(AnalysisError, match="unknown gate"):
        _registry().validate_gate("non_typing")


@pytest.mark.parametrize(
    "arguments",
    [
        {"views": None},
        {"checks": None},
        {"check_selection": None},
        {"check_selection": []},
        {"check_selection": {"structural": None}},
        {"check_selection": {"structural": "cycles"}},
        {"check_selection": {"structural": (None,)}},
        {"check_selection": {"structural": (["cycles"],)}},
        {"check_selection": {None: ()}},
        {"checks": (CheckRegistration("custom", AdvisoryCheck(), (None, [])),)},
    ],
)
def test_malformed_registry_values_raise_analysis_errors(arguments):
    defaults = {
        "views": (ViewRegistration("structural", StructuralView()),),
        "checks": (CheckRegistration("cycles", CycleCheck()),),
    }
    defaults.update(arguments)
    with pytest.raises(AnalysisError):
        StrategyRegistry(**defaults)
