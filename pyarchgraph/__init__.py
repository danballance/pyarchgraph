"""Construct analyzers and explicitly compose immutable analysis strategies."""

from pyarchgraph.adapters.rendering import JsonReportRenderer
from pyarchgraph.application.ports import ProjectAnalyzer
from pyarchgraph.application.strategies import (
    CheckRegistration,
    StrategyRegistry,
    ViewRegistration,
)
from pyarchgraph.composition import ApplicationFactory
from pyarchgraph.domain.errors import AnalysisError, ExtensionError
from pyarchgraph.domain.graph import (
    AnalysisSnapshot,
    CheckContext,
    ViewEdge,
    ViewEvidence,
    ViewGraph,
    ViewNode,
)
from pyarchgraph.domain.model import (
    AnalysisOptions,
    AnalysisReport,
    CheckResult,
    RuleFinding,
    Severity,
    TargetDeclaration,
)
from pyarchgraph.domain.strategies import CheckStrategy, GraphViewStrategy

__version__ = "0.7.0"

__all__ = [
    "AnalysisError",
    "AnalysisOptions",
    "AnalysisReport",
    "AnalysisSnapshot",
    "ApplicationFactory",
    "CheckContext",
    "CheckRegistration",
    "CheckResult",
    "CheckStrategy",
    "ExtensionError",
    "GraphViewStrategy",
    "JsonReportRenderer",
    "ProjectAnalyzer",
    "RuleFinding",
    "Severity",
    "StrategyRegistry",
    "TargetDeclaration",
    "ViewEdge",
    "ViewEvidence",
    "ViewGraph",
    "ViewNode",
    "ViewRegistration",
]
