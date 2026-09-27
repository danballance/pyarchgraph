import json

import pytest

from pyarchgraph.adapters.configuration import TomlOptionsReader
from pyarchgraph.application.validation import OptionValidator
from pyarchgraph.composition import ApplicationFactory
from pyarchgraph.domain.model import AnalysisOptions, TargetDeclaration


def test_explicit_config_parses_ownership_and_relative_boundary_path(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "config"
    folder.mkdir()
    path = folder / "analysis.toml"
    path.write_text(
        'owned_prefixes = ["acme"]\n[[targets]]\nname="acme.native"\nkind="native"\nreason="Built separately"\npath="../native.pyx"\nacknowledged=true\n'
    )
    options = TomlOptionsReader(OptionValidator()).load(
        path, excludes=("generated",), gate="module-body", details="component-edges"
    )
    assert options.owned_prefixes == ("acme",)
    assert options.targets[0].path == "native.pyx"
    assert options.targets[0].acknowledged
    assert options.gate == "module-body" and options.details == "component-edges"


@pytest.mark.parametrize(
    "contents",
    [
        "unknown=true",
        'owned_prefixes="acme"',
        'targets={name="acme"}',
        '[[targets]]\nname="pkg.x"\nkind="native"',
        '[[targets]]\nname="pkg.x"\nkind="native"\nreason=""',
        '[[targets]]\nname="pkg.x"\nkind="native"\nreason="why"\nacknowledged="true"',
        'owned_prefixes=["pkg..x"]',
        '[[targets]]\nname="pkg.x"\nkind="excluded"\nreason="why"',
    ],
)
def test_invalid_config_is_rejected_before_analysis(tmp_path, contents):
    path = tmp_path / "config.toml"
    path.write_text(contents)
    with pytest.raises(ValueError):
        TomlOptionsReader(OptionValidator()).load(
            path, excludes=(), gate="structural", details="summary"
        )


@pytest.mark.parametrize(
    "options",
    [
        AnalysisOptions(details="all"),
        AnalysisOptions(excludes=["bad"]),
        AnalysisOptions(owned_prefixes=("bad/name",)),
        AnalysisOptions(
            targets=(
                TargetDeclaration("x", "generated", "reason"),
                TargetDeclaration("x", "native", "reason"),
            )
        ),
    ],
)
def test_invalid_api_options_are_rejected(options):
    with pytest.raises(ValueError):
        OptionValidator().validate(options)


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
            (tmp_path / "absent",), options=AnalysisOptions(gate="runtime")
        )
