"""Configuration affects the public CLI and request boundary explicitly."""

import json

import pytest

from pyarchgraph.application.requests import AnalysisOptions, AnalysisRequest
from pyarchgraph.main import ApplicationFactory


def test_cli_boundary_acknowledgement_and_gate_never_bypass_missing_coverage(
    tmp_path, capsys
):
    (tmp_path / "app.py").write_text(
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import generated\n"
    )
    config = tmp_path / "scope.toml"
    config.write_text(
        '[[targets]]\nname="generated"\nkind="generated"\nreason="Created by release build"\n'
    )
    assert (
        ApplicationFactory()
        .create_cli()
        .run([str(tmp_path), "--config", str(config), "--gate", "module-body"])
        == 2
    )
    before = json.loads(capsys.readouterr().out)
    assert before["views"]["module-body"]["findings"] == []
    config.write_text(config.read_text() + "acknowledged=true\n")
    assert (
        ApplicationFactory()
        .create_cli()
        .run([str(tmp_path), "--config", str(config), "--gate", "module-body"])
        == 0
    )
    after = json.loads(capsys.readouterr().out)
    assert after["coverage"]["boundaries"][0]["acknowledged"]


def test_config_is_not_discovered_implicitly(tmp_path, capsys):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "pyarchgraph.toml").write_text("not valid toml {")
    assert ApplicationFactory().create_cli().run([str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "complete"


def test_unknown_gate_is_rejected_before_discovery(tmp_path):
    with pytest.raises(ValueError, match="gate"):
        ApplicationFactory().create_analyzer().analyse(
            AnalysisRequest(
                (tmp_path / "absent",), options=AnalysisOptions(gate="runtime")
            )
        )
