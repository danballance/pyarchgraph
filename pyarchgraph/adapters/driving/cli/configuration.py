"""Read explicit ownership/boundary configuration from TOML."""

import os
import tomllib
from pathlib import Path

from pyarchgraph.application.requests import AnalysisOptions
from pyarchgraph.domain.models import TargetDeclaration


class TomlOptionsReader:
    def load(
        self,
        config_path: Path | None,
        *,
        excludes: tuple[str, ...],
        gate: str,
        details: str,
        base_dir: Path | None = None,
    ) -> AnalysisOptions:
        """Read only the explicitly selected file; paths are relative to that file."""
        base = Path.cwd() if base_dir is None else Path(base_dir)
        base = base.resolve()
        if config_path is not None:
            config_path = base / config_path
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
                        os.path.relpath(config_path.parent / path, base)
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
        return options
