"""Application option validation is independent of adapters."""

import pytest

from pyarchgraph.application.requests import AnalysisOptions
from pyarchgraph.application.validation import OptionValidator
from pyarchgraph.domain.models import TargetDeclaration


@pytest.mark.parametrize(
    "options",
    [
        AnalysisOptions(details="all"),
        AnalysisOptions(excludes=["bad"]),
        AnalysisOptions(owned_prefixes=("bad/name",)),
        AnalysisOptions(owned_prefixes=("pkg..x",)),
        AnalysisOptions(targets=(TargetDeclaration("pkg.x", "native", ""),)),
        AnalysisOptions(
            targets=(TargetDeclaration("pkg.x", "native", "why", acknowledged="true"),)
        ),
        AnalysisOptions(targets=(TargetDeclaration("pkg.x", "excluded", "why"),)),
        AnalysisOptions(
            targets=(
                TargetDeclaration("x", "generated", "reason"),
                TargetDeclaration("x", "native", "reason"),
            )
        ),
    ],
)
def test_invalid_api_options_are_rejected(options):
    with pytest.raises(ValueError):
        OptionValidator().validate(options)
