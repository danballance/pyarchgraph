"""Explicit TOML configuration for ownership and accepted implementation boundaries."""

import os
from pathlib import Path, PurePosixPath

import tomllib

from pyarchgraph.model import AnalysisOptions, TargetDeclaration


def _module_name(value: object) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and all(
            part and not any(char.isspace() or char in "/\\:" for char in part)
            for part in value.split(".")
        )
    )


def validate_options(options: AnalysisOptions) -> None:
    if not isinstance(options, AnalysisOptions):
        raise ValueError("options must be an AnalysisOptions instance")
    if options.gate not in ("structural", "non-typing", "module-body"):
        raise ValueError("gate must be structural, non-typing, or module-body")
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
            raise ValueError("exclude patterns must be nonempty POSIX-relative strings")
        PurePosixPath("validation-path").match(pattern)
    if not isinstance(options.owned_prefixes, tuple) or not all(
        _module_name(name) for name in options.owned_prefixes
    ):
        raise ValueError("owned_prefixes must be a tuple of dotted import prefixes")
    if not isinstance(options.targets, tuple):
        raise ValueError("targets must be a tuple of TargetDeclaration values")
    seen = set()
    for target in options.targets:
        if not isinstance(target, TargetDeclaration):
            raise ValueError("targets must contain TargetDeclaration values")
        if not _module_name(target.name) or target.name in seen:
            raise ValueError("target names must be valid, unique dotted import names")
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


def load_options(
    config_path: Path | None,
    *,
    excludes: tuple[str, ...],
    gate: str,
    details: str,
) -> AnalysisOptions:
    """Read only the explicitly selected file; paths are relative to that file."""
    prefixes: tuple[str, ...] = ()
    targets: tuple[TargetDeclaration, ...] = ()
    if config_path is not None:
        with config_path.open("rb") as stream:
            data = tomllib.load(stream)
        if set(data) - {"owned_prefixes", "targets"}:
            raise ValueError("config supports only owned_prefixes and targets")
        raw_prefixes = data.get("owned_prefixes", [])
        raw_targets = data.get("targets", [])
        if not isinstance(raw_prefixes, list) or not isinstance(raw_targets, list):
            raise ValueError("owned_prefixes and targets must be arrays")
        prefixes = tuple(raw_prefixes)
        parsed = []
        for record in raw_targets:
            if (
                not isinstance(record, dict)
                or set(record) - {"name", "kind", "reason", "path", "acknowledged"}
                or not {"name", "kind", "reason"} <= set(record)
            ):
                raise ValueError(
                    "each target needs name, kind, reason and optional path/acknowledged"
                )
            path = record.get("path")
            if path is not None:
                if not isinstance(path, str) or not path:
                    raise ValueError("target path must be a nonempty string")
                path = Path(
                    os.path.relpath(config_path.parent / path, Path.cwd())
                ).as_posix()
            parsed.append(
                TargetDeclaration(
                    name=record["name"],
                    kind=record["kind"],
                    reason=record["reason"],
                    path=path,
                    acknowledged=record.get("acknowledged", False),
                )
            )
        targets = tuple(parsed)
    options = AnalysisOptions(
        excludes=excludes,
        gate=gate,
        details=details,
        owned_prefixes=prefixes,
        targets=targets,
    )
    validate_options(options)
    return options
