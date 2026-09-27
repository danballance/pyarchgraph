"""Validate requests before invoking any source or metadata adapter."""

from pathlib import PurePosixPath
from pyarchgraph.domain.model import AnalysisOptions, TargetDeclaration


class OptionValidator:
    def __init__(
        self, gates: tuple[str, ...] = ("structural", "non-typing", "module-body")
    ) -> None:
        self.gates = gates

    @staticmethod
    def _module_name(value: object) -> bool:
        return (
            isinstance(value, str)
            and bool(value)
            and all(
                part and not any(char.isspace() or char in "/\\:" for char in part)
                for part in value.split(".")
            )
        )

    def validate(self, options: AnalysisOptions) -> None:
        if not isinstance(options, AnalysisOptions):
            raise ValueError("options must be an AnalysisOptions instance")
        if not isinstance(options.gate, str) or options.gate not in self.gates:
            raise ValueError("gate must be a nonempty registered view ID")
        if options.details not in ("summary", "component-edges"):
            raise ValueError("details must be summary or component-edges")
        if not isinstance(options.excludes, tuple):
            raise ValueError("excludes must be a tuple of relative glob strings")
        for pattern in options.excludes:
            if (
                not isinstance(pattern, str)
                or not pattern
                or PurePosixPath(pattern).is_absolute()
            ):
                raise ValueError(
                    "exclude patterns must be nonempty POSIX-relative strings"
                )
            PurePosixPath("validation-path").match(pattern)
        if not isinstance(options.owned_prefixes, tuple) or not all(
            self._module_name(name) for name in options.owned_prefixes
        ):
            raise ValueError("owned_prefixes must be a tuple of dotted import prefixes")
        if not isinstance(options.targets, tuple):
            raise ValueError("targets must be a tuple of TargetDeclaration values")
        seen = set()
        for target in options.targets:
            if not isinstance(target, TargetDeclaration):
                raise ValueError("targets must contain TargetDeclaration values")
            if not self._module_name(target.name) or target.name in seen:
                raise ValueError(
                    "target names must be valid, unique dotted import names"
                )
            seen.add(target.name)
            if target.kind not in ("stub", "native", "generated"):
                raise ValueError(
                    "configured target kind must be stub, native, or generated"
                )
            if not isinstance(target.reason, str) or not target.reason.strip():
                raise ValueError("each target requires a nonempty reason")
            if type(target.acknowledged) is not bool:
                raise ValueError("target acknowledged must be a boolean")
            if target.path is not None and (
                not isinstance(target.path, str) or not target.path
            ):
                raise ValueError("target path must be a nonempty string or null")
