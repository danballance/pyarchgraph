import pytest

from pyarchgraph.adapters.driving.cli.configuration import TomlOptionsReader


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
    options = TomlOptionsReader().load(
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
    ],
)
def test_invalid_config_is_rejected_before_analysis(tmp_path, contents):
    path = tmp_path / "config.toml"
    path.write_text(contents)
    with pytest.raises(ValueError):
        TomlOptionsReader().load(
            path, excludes=(), gate="structural", details="summary"
        )
