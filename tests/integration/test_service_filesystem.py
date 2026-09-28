"""A composed analysis captures filesystem context once per request."""

from pathlib import Path
from unittest.mock import patch

import pytest

from pyarchgraph.adapters.driven.filesystem.discovery import FileSystemSourceDiscovery
from pyarchgraph.adapters.driving.cli.application import CliExitCodePolicy
from pyarchgraph.application.ports.sources import SourceDiscovery
from pyarchgraph.application.requests import AnalysisOptions, AnalysisRequest
from pyarchgraph.main import ApplicationFactory


def test_captured_base_survives_cwd_changes_and_reused_service_root_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    source = project / "src"
    source.mkdir(parents=True)
    (source / "sample.py").write_text("import sample\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    class RelocatingDiscovery(SourceDiscovery):
        def discover(self, root, *, excludes=(), pruned_directories=()):
            result = FileSystemSourceDiscovery().discover(
                root, excludes=excludes, pruned_directories=pruned_directories
            )
            monkeypatch.chdir(elsewhere)
            return result

    analyzer = ApplicationFactory(discovery=RelocatingDiscovery()).create_analyzer()
    monkeypatch.chdir(project)
    with patch.object(Path, "cwd", wraps=Path.cwd) as cwd:
        initial = analyzer.analyse(
            AnalysisRequest((Path("src"),), options=AnalysisOptions())
        )
        assert cwd.call_count == 1
    assert Path.cwd() == elsewhere
    assert initial.coverage.roots == ("src",)
    assert initial.sources[0].path == "src/sample.py"
    assert CliExitCodePolicy().exit_code(initial) == 1

    with patch.object(Path, "cwd", side_effect=AssertionError("unexpected cwd read")):
        explicit = analyzer.analyse(
            AnalysisRequest((Path("src"),), options=AnalysisOptions(), base_dir=project)
        )
        assert explicit == initial
        with pytest.raises(ValueError, match="existing directory"):
            analyzer.analyse(
                AnalysisRequest(
                    (Path("missing"),), options=AnalysisOptions(), base_dir=project
                )
            )
        assert (
            analyzer.analyse(
                AnalysisRequest(
                    (Path("src"),), options=AnalysisOptions(), base_dir=project
                )
            )
            == initial
        )
