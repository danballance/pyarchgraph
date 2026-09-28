"""The incoming project-analysis contract."""

from typing import Protocol

from pyarchgraph.application.requests import AnalysisRequest
from pyarchgraph.application.results import AnalysisReport


class ProjectAnalyzer(Protocol):
    """Defines how callers request a project analysis and receive its report."""

    def analyse(self, request: AnalysisRequest) -> AnalysisReport: ...
