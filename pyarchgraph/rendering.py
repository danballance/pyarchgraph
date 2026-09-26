"""Serialize the sole public report format."""

import json
from dataclasses import asdict

from pyarchgraph.model import AnalysisReport


def render_json(report: AnalysisReport) -> str:
    return (
        json.dumps(
            asdict(report),
            sort_keys=True,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    )
