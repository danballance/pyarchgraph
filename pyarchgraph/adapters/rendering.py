"""JSON rendering of immutable reports, including extension findings."""

import json
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum

from pyarchgraph.domain.model import AnalysisReport


class JsonReportRenderer:
    _SCALAR_TYPES = frozenset((str, int, float, bool, type(None)))

    def _value(self, value: object) -> object:
        # Most report fields are already JSON scalars. Use exact types so string
        # enums still reach the enum conversion below.
        if type(value) in self._SCALAR_TYPES:
            return value
        if is_dataclass(value) and not isinstance(value, type):
            return {
                field.name: self._value(getattr(value, field.name))
                for field in fields(value)
            }
        if isinstance(value, Mapping):
            return {key: self._value(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [self._value(item) for item in value]
        if isinstance(value, Enum):
            return value.value
        return value

    def render(self, report: AnalysisReport) -> str:
        return (
            json.dumps(
                self._value(report),
                sort_keys=True,
                indent=2,
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        )
