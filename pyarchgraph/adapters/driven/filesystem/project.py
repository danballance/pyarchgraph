"""Filesystem project locations and literal packaging metadata readers."""

import configparser
import os
import tomllib
from pathlib import Path

from pyarchgraph.application.exceptions import AnalysisError
from pyarchgraph.application.ports.project import (
    DirectoryLocation,
    ProjectAccess,
    ProjectLocation,
    ProjectMetadata,
)


class FileSystemProjectAccess(ProjectAccess):
    """Capture one base directory and keep all physical path operations outside core."""

    def locate(
        self, source_roots: tuple[Path, ...], base_dir: Path | None = None
    ) -> ProjectLocation:
        base = Path.cwd() if base_dir is None else Path(base_dir)
        base = base.resolve()
        roots = tuple(
            sorted({(base / Path(root)).resolve() for root in source_roots}, key=str)
        )
        if any(not root.is_dir() for root in roots):
            raise AnalysisError("every source root must be an existing directory")
        return ProjectLocation(base, roots)

    def relative(self, path: Path, base_dir: Path) -> str:
        return Path(os.path.relpath(path, base_dir)).as_posix()

    def absolute(self, path: str, base_dir: Path) -> Path:
        return Path(os.path.abspath(base_dir / path))

    def directory(self, root: Path, candidate: str) -> DirectoryLocation:
        path = (root / candidate).resolve()
        return DirectoryLocation(path, path.is_dir())

    def read_metadata(self, root: Path) -> ProjectMetadata:
        """Read literal setuptools roots and package aliases without build execution."""
        candidates: set[str] = set()
        package_dirs: dict[str, str] = {}
        pyproject = root / "pyproject.toml"
        if pyproject.is_file():
            try:
                with pyproject.open("rb") as stream:
                    data = tomllib.load(stream)
                settings = data.get("tool", {}).get("setuptools", {})
                package_dir = settings.get("package-dir", {})
                if isinstance(package_dir, dict):
                    package_dirs.update(
                        (name, value)
                        for name, value in package_dir.items()
                        if isinstance(name, str) and isinstance(value, str)
                    )
                packages = settings.get("packages", {})
                if isinstance(packages, dict):
                    where = packages.get("find", {}).get("where", [])
                    if isinstance(where, list):
                        candidates.update(
                            value for value in where if isinstance(value, str)
                        )
            except (OSError, ValueError, AttributeError, TypeError):
                pass
        setup_cfg = root / "setup.cfg"
        if setup_cfg.is_file():
            parser = configparser.ConfigParser(interpolation=None)
            try:
                parser.read(setup_cfg, encoding="utf-8")
                if parser.has_option("options", "package_dir"):
                    for line in parser.get("options", "package_dir").splitlines():
                        if "=" in line:
                            name, _, value = line.partition("=")
                            package_dirs[name.strip()] = value.strip()
                if parser.has_option("options.packages.find", "where"):
                    candidates.update(
                        parser.get("options.packages.find", "where").splitlines()
                    )
            except (OSError, ValueError, configparser.Error):
                pass

        aliases = {}
        for name, value in package_dirs.items():
            if not self._relative_directory(value):
                continue
            path = Path(value)
            if not name:
                candidates.add(value)
                continue
            package_parts = tuple(name.split("."))
            if path.parts[-len(package_parts) :] == package_parts:
                candidates.add(Path(*path.parts[: -len(package_parts)]).as_posix())
            else:
                # Renaming a directory as a package cannot be represented merely
                # by selecting another import root. Keep the alias for diagnostics.
                aliases[name] = path.as_posix()
        candidates = {
            Path(value.strip()).as_posix().strip("/")
            for value in candidates
            if self._relative_directory(value.strip())
            and Path(value.strip()).as_posix() != "."
        }
        return ProjectMetadata(
            tuple(sorted(candidates)), tuple(sorted(aliases.items()))
        )

    @staticmethod
    def _relative_directory(value: str) -> bool:
        return (
            bool(value)
            and not Path(value).is_absolute()
            and ".." not in Path(value).parts
        )
