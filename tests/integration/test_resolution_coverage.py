"""Regressions for source identities and explicit resolution boundaries."""

from dataclasses import replace
from pathlib import Path

from pyarchgraph.adapters.driven.filesystem.discovery import FileSystemSourceDiscovery
from pyarchgraph.domain.canonicalization import FactCanonicalizer
from pyarchgraph.domain.models import (
    ExternalClassification,
    ResolutionKind,
    Severity,
    TargetDeclaration,
    UnresolvedReason,
)
from pyarchgraph.domain.resolution import (
    ArchitectureDependencyPolicy,
    StaticImportResolver,
)
from pyarchgraph.main import ApplicationFactory


def _resolve(
    root: Path, files: dict[str, str], *, owned=(), declarations=(), excludes=()
):
    for name, source in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    inventory = FileSystemSourceDiscovery().discover(root, excludes=excludes)
    collection = (
        ApplicationFactory().create_fact_source().collect(root, inventory.modules)
    )
    assert not collection.diagnostics
    result = StaticImportResolver().resolve(
        FactCanonicalizer().canonicalise(collection.facts),
        inventory.modules,
        inventory.namespace_prefixes,
        owned_prefixes=owned,
        targets=inventory.targets + declarations,
    )
    return inventory, collection, result


def _pairs(result):
    return {(edge.source, edge.target) for edge in result.dependencies}


def test_ordinary_module_from_self_imports_survive_all_spellings(
    tmp_path: Path,
) -> None:
    _, _, result = _resolve(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/mod.py": "from pkg.mod import value, other\nfrom .mod import value\nfrom pkg.mod import *\n",
        },
    )
    (edge,) = result.dependencies
    assert (edge.source, edge.target) == ("source:pkg/mod.py", "source:pkg/mod.py")
    assert len(edge.evidence) == 4
    assert all(
        item.resolution_kind is ResolutionKind.EXACT_BASE for item in edge.evidence
    )
    assert (
        ArchitectureDependencyPolicy().select(result.dependencies)
        == result.dependencies
    )


def test_initializer_suppression_and_child_normalization_use_source_ids(
    tmp_path: Path,
) -> None:
    _, _, result = _resolve(
        tmp_path,
        {
            "pkg/__init__.py": "from . import child\n",
            "pkg/child.py": "",
            "app.py": "from pkg import child\nimport pkg\n",
        },
    )
    selected = ArchitectureDependencyPolicy().select(result.dependencies)
    pairs = {(edge.source, edge.target): edge for edge in selected}
    assert set(pairs) == {
        ("source:pkg/__init__.py", "source:pkg/child.py"),
        ("source:app.py", "source:pkg/child.py"),
        ("source:app.py", "source:pkg/__init__.py"),
    }
    assert len(pairs[("source:app.py", "source:pkg/__init__.py")].evidence) == 1
    assert (
        pairs[("source:app.py", "source:pkg/__init__.py")].evidence[0].resolution_kind
        is ResolutionKind.EXACT_MODULE
    )


def test_shared_namespace_does_not_own_external_sibling_branches(
    tmp_path: Path,
) -> None:
    _, _, result = _resolve(
        tmp_path,
        {
            "google/api_core/__init__.py": "",
            "google/api_core/client.py": "import google.auth\nfrom google.protobuf import message\nimport google.api_core.missing\n",
        },
    )
    assert [
        (item.requested, item.classification) for item in result.external_imports
    ] == [
        ("google.auth", ExternalClassification.EXTERNAL_UNKNOWN),
        ("google.protobuf", ExternalClassification.EXTERNAL_UNKNOWN),
    ]
    assert [(item.requested, item.reason) for item in result.unresolved_imports] == [
        ("google.api_core.missing", UnresolvedReason.MISSING_INTERNAL_TARGET),
    ]


def test_pure_namespace_ownership_requires_an_explicit_prefix(tmp_path: Path) -> None:
    inventory, collection, conservative = _resolve(
        tmp_path,
        {
            "app.py": "import ns.missing\n",
            "ns/leaf.py": "",
        },
    )
    assert conservative.unresolved_imports == ()
    assert conservative.external_imports[0].requested == "ns.missing"
    explicit = StaticImportResolver().resolve(
        FactCanonicalizer().canonicalise(collection.facts),
        inventory.modules,
        inventory.namespace_prefixes,
        owned_prefixes=("ns",),
    )
    assert explicit.external_imports == ()
    assert (
        explicit.unresolved_imports[0].reason
        is UnresolvedReason.MISSING_INTERNAL_TARGET
    )


def test_unusual_dotted_names_keep_their_relative_package_context(
    tmp_path: Path,
) -> None:
    _, _, result = _resolve(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/helper.py": "",
            "pkg/0001_initial.py": "from .helper import VALUE\n",
            "pkg/is/__init__.py": "",
            "pkg/is/formats.py": "from ..helper import VALUE\n",
            "pkg/bad-name.py": "from .helper import VALUE\n",
        },
    )
    assert _pairs(result) == {
        ("source:pkg/0001_initial.py", "source:pkg/helper.py"),
        ("source:pkg/is/formats.py", "source:pkg/helper.py"),
        ("source:pkg/bad-name.py", "source:pkg/helper.py"),
    }
    assert result.diagnostics == result.unresolved_imports == ()


def test_path_only_sources_preserve_absolute_edges_and_diagnose_relative_context(
    tmp_path: Path,
) -> None:
    _, _, result = _resolve(
        tmp_path,
        {
            "helper.py": "",
            ".hidden/tool.py": "import helper\nif True:\n    from . import local\n",
        },
    )
    assert _pairs(result) == {("source:.hidden/tool.py", "source:helper.py")}
    assert (
        result.unresolved_imports[0].reason is UnresolvedReason.UNKNOWN_PACKAGE_CONTEXT
    )
    (diagnostic,) = result.diagnostics
    assert (
        diagnostic.code,
        diagnostic.line,
        diagnostic.column,
        diagnostic.severity,
    ) == ("unknown_package_context", 3, 5, Severity.ERROR)


def test_shadowed_source_is_parsed_but_package_owns_incoming_imports(
    tmp_path: Path,
) -> None:
    _, _, result = _resolve(
        tmp_path,
        {
            "app.py": "import pkg\n",
            "pkg.py": "import helper\n",
            "pkg/__init__.py": "",
            "helper.py": "",
        },
    )
    assert _pairs(result) == {
        ("source:app.py", "source:pkg/__init__.py"),
        ("source:pkg.py", "source:helper.py"),
    }
    assert result.diagnostics == ()


def test_stub_native_and_generated_targets_are_boundaries_not_missing_sources(
    tmp_path: Path,
) -> None:
    _, _, result = _resolve(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/app.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import pkg.stub\nimport pkg.native\nimport pkg.generated\n",
            "pkg/stub.pyi": "class Thing: ...\n",
            "pkg/native.pyx": "",
        },
        declarations=(
            TargetDeclaration(
                "pkg.generated", "generated", "Built by the project generator."
            ),
        ),
    )
    assert result.unresolved_imports == ()
    assert [
        (item.name, item.kind, item.acknowledged) for item in result.boundaries
    ] == [
        ("pkg.generated", "generated", False),
        ("pkg.native", "native", False),
        ("pkg.stub", "stub", False),
    ]
    assert result.boundaries[-1].evidence[0].context.typing_only
    assert result.boundaries[-1].evidence[0].column == 5
    assert result.dependencies == ()


def test_boundary_acknowledgement_preserves_discovered_path_and_evidence(
    tmp_path: Path,
) -> None:
    _, _, result = _resolve(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/app.py": "import pkg.native\n",
            "pkg/native.pyx": "",
        },
        declarations=(
            TargetDeclaration(
                "pkg.native", "native", "Accepted native boundary.", acknowledged=True
            ),
        ),
    )
    (boundary,) = result.boundaries
    assert boundary.acknowledged
    assert boundary.path == "pkg/native.pyx"
    assert "Accepted native boundary." in boundary.reason
    assert boundary.evidence[0].path == "pkg/app.py"


def test_python_implementation_wins_over_stub_and_unbuilt_native_companions(
    tmp_path: Path,
) -> None:
    _, _, result = _resolve(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/app.py": "import pkg.target\n",
            "pkg/target.py": "",
            "pkg/target.pyi": "",
            "pkg/target.pyx": "",
        },
    )
    assert _pairs(result) == {("source:pkg/app.py", "source:pkg/target.py")}
    assert result.boundaries == result.diagnostics == ()


def test_native_artifact_competing_with_python_source_cannot_be_acknowledged(
    tmp_path: Path,
) -> None:
    _, _, result = _resolve(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/app.py": "import pkg.target\n",
            "pkg/target.py": "",
            "pkg/target.so": "",
        },
        declarations=(
            TargetDeclaration(
                "pkg.target", "native", "Accepted boundary.", acknowledged=True
            ),
        ),
    )
    assert result.dependencies == result.boundaries == ()
    assert result.unresolved_imports[0].reason is UnresolvedReason.AMBIGUOUS_TARGET
    assert all(
        item.code == "ambiguous_target" and item.severity is Severity.ERROR
        for item in result.diagnostics
    )


def test_distinct_native_locations_are_ambiguous_even_when_unused(
    tmp_path: Path,
) -> None:
    _, _, result = _resolve(
        tmp_path,
        {"app.py": ""},
        declarations=(
            TargetDeclaration(
                "pkg.native", "native", "First root.", "root_a/pkg/native.pyx", True
            ),
            TargetDeclaration(
                "pkg.native", "native", "Second root.", "root_b/pkg/native.pyx", True
            ),
        ),
    )
    assert result.boundaries == ()
    assert result.diagnostics[0].code == "ambiguous_target"


def test_companion_stub_and_native_target_use_one_native_boundary(
    tmp_path: Path,
) -> None:
    _, _, result = _resolve(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/app.py": "import pkg.target\n",
            "pkg/target.pyi": "",
            "pkg/target.pyx": "",
            "pkg/target.so": "",
        },
    )
    assert [(item.name, item.kind) for item in result.boundaries] == [
        ("pkg.target", "native")
    ]
    assert result.unresolved_imports == result.diagnostics == ()


def test_excluded_directory_is_an_acknowledged_prefix_boundary(tmp_path: Path) -> None:
    _, _, result = _resolve(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/app.py": "import pkg.generated.deep\n",
            "pkg/generated/deep.py": "invalid syntax !",
        },
        excludes=("pkg/generated",),
    )
    assert result.unresolved_imports == ()
    (boundary,) = result.boundaries
    assert (boundary.name, boundary.kind, boundary.path, boundary.acknowledged) == (
        "pkg.generated.deep",
        "excluded",
        "pkg/generated/",
        True,
    )


def test_cross_root_ambiguity_propagates_to_child_imports(tmp_path: Path) -> None:
    inventory, collection, _ = _resolve(
        tmp_path,
        {
            "app.py": "import pkg.child\n",
            "pkg/__init__.py": "",
            "pkg/child.py": "",
        },
    )
    package = next(
        module for module in inventory.modules if module.import_name == "pkg"
    )
    extra = replace(
        package, id="source:other/pkg/__init__.py", path="other/pkg/__init__.py"
    )
    result = StaticImportResolver().resolve(
        FactCanonicalizer().canonicalise(collection.facts),
        (*inventory.modules, extra),
        inventory.namespace_prefixes,
    )
    assert result.dependencies == ()
    assert result.unresolved_imports[0].reason is UnresolvedReason.AMBIGUOUS_TARGET
    assert result.diagnostics[0].code == "ambiguous_target"


def test_exact_excluded_file_does_not_claim_unrelated_descendants(
    tmp_path: Path,
) -> None:
    _, _, result = _resolve(
        tmp_path,
        {
            "app.py": "import unavailable.extra\n",
            "unavailable.py": "",
        },
        excludes=("unavailable.py",),
    )
    assert result.boundaries == ()
    assert result.external_imports[0].requested == "unavailable.extra"


def test_ambiguous_package_does_not_invent_an_ambiguous_attribute_module(
    tmp_path: Path,
) -> None:
    inventory, collection, _ = _resolve(
        tmp_path,
        {
            "app.py": "from pkg import VALUE\n",
            "pkg/__init__.py": "VALUE = 1\n",
        },
    )
    package = next(
        module for module in inventory.modules if module.import_name == "pkg"
    )
    extra = replace(
        package, id="source:other/pkg/__init__.py", path="other/pkg/__init__.py"
    )
    result = StaticImportResolver().resolve(
        FactCanonicalizer().canonicalise(collection.facts),
        (*inventory.modules, extra),
        inventory.namespace_prefixes,
    )
    assert [(item.requested, item.reason) for item in result.unresolved_imports] == [
        ("pkg", UnresolvedReason.AMBIGUOUS_TARGET)
    ]
