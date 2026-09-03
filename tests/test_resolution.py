from __future__ import annotations

from pyarchgraph.model import (
    ExternalClassification,
    ImportFact,
    ImportScope,
    ImportSyntax,
    ResolutionKind,
    SourceModule,
    UnresolvedReason,
)
from pyarchgraph.resolution import resolve_imports


def _module(module_id: str, *, package: bool = False) -> SourceModule:
    stem = module_id.replace(".", "/")
    return SourceModule(
        id=module_id,
        path=f"{stem}/__init__.py" if package else f"{stem}.py",
        is_package=package,
        parent_package=module_id.rpartition(".")[0] or None,
    )


def _fact(
    fact_id: str,
    source: str,
    *,
    syntax: ImportSyntax = ImportSyntax.IMPORT,
    base: str | None,
    name: str | None = None,
    level: int = 0,
) -> ImportFact:
    return ImportFact(
        id=fact_id,
        source=source,
        path=f"{source.replace('.', '/')}.py",
        line=1,
        column=0,
        end_line=1,
        end_column=1,
        alias_index=0,
        syntax=syntax,
        source_segment=None,
        base_module=base,
        imported_name=name,
        as_name=None,
        bound_name=name or (base or "").partition(".")[0],
        relative_level=level,
        scope=ImportScope.MODULE,
        type_only=False,
    )


def _dependency_map(result):
    return {
        (edge.source, edge.target): tuple(
            (item.fact_id, item.resolution_kind) for item in edge.evidence
        )
        for edge in result.dependencies
    }


def test_import_resolution_is_exact_and_aggregates_all_evidence() -> None:
    modules = (
        _module("app"),
        _module("pkg", package=True),
        _module("pkg.child"),
        _module("json"),
    )
    facts = (
        _fact("fact-z", "app", base="pkg.child"),
        _fact("fact-a", "app", base="pkg.child"),
        _fact("fact-self", "app", base="app"),
        _fact("fact-json", "app", base="json"),
    )

    result = resolve_imports(facts, modules, ())

    assert _dependency_map(result) == {
        ("app", "app"): (("fact-self", ResolutionKind.EXACT_MODULE),),
        ("app", "json"): (("fact-json", ResolutionKind.EXACT_MODULE),),
        ("app", "pkg.child"): (
            ("fact-a", ResolutionKind.EXACT_MODULE),
            ("fact-z", ResolutionKind.EXACT_MODULE),
        ),
    }
    assert result.external_imports == ()
    assert result.unresolved_imports == ()


def test_from_import_keeps_exact_base_and_probable_leaf_separate() -> None:
    modules = (
        _module("app"),
        _module("p", package=True),
        _module("p.x"),
    )
    facts = (
        _fact(
            "fact-x",
            "app",
            syntax=ImportSyntax.IMPORT_FROM,
            base="p",
            name="x",
        ),
        _fact(
            "fact-attr",
            "app",
            syntax=ImportSyntax.IMPORT_FROM,
            base="p",
            name="attribute",
        ),
        _fact(
            "fact-star",
            "app",
            syntax=ImportSyntax.IMPORT_FROM,
            base="p",
            name="*",
        ),
    )

    result = resolve_imports(facts, modules, ())

    assert _dependency_map(result) == {
        ("app", "p"): (
            ("fact-attr", ResolutionKind.EXACT_BASE),
            ("fact-star", ResolutionKind.EXACT_BASE),
            ("fact-x", ResolutionKind.EXACT_BASE),
        ),
        ("app", "p.x"): (("fact-x", ResolutionKind.PROBABLE_SUBMODULE),),
    }
    assert result.unresolved_imports == ()


def test_from_import_suppresses_only_the_trivial_base_self_edge() -> None:
    modules = (_module("p", package=True), _module("p.x"))
    package_fact = _fact(
        "fact-package",
        "p",
        syntax=ImportSyntax.IMPORT_FROM,
        base=None,
        name="x",
        level=1,
    )
    leaf_fact = _fact(
        "fact-leaf",
        "p.x",
        syntax=ImportSyntax.IMPORT_FROM,
        base="p",
        name="x",
    )

    result = resolve_imports((package_fact, leaf_fact), modules, ())

    assert _dependency_map(result) == {
        ("p", "p.x"): (("fact-package", ResolutionKind.PROBABLE_SUBMODULE),),
        ("p.x", "p"): (("fact-leaf", ResolutionKind.EXACT_BASE),),
        ("p.x", "p.x"): (("fact-leaf", ResolutionKind.PROBABLE_SUBMODULE),),
    }


def test_namespace_bases_are_unmodelled_but_indexed_leaf_is_probable() -> None:
    modules = (_module("app"), _module("ns.leaf"))
    facts = (
        _fact("fact-import-ns", "app", base="ns"),
        _fact(
            "fact-from-ns",
            "app",
            syntax=ImportSyntax.IMPORT_FROM,
            base="ns",
            name="leaf",
        ),
        _fact("fact-missing", "app", base="ns.missing"),
    )

    result = resolve_imports(facts, modules, ("ns",))

    assert _dependency_map(result) == {
        ("app", "ns.leaf"): (("fact-from-ns", ResolutionKind.PROBABLE_SUBMODULE),)
    }
    assert [
        (item.requested, item.reason, item.fact_ids)
        for item in result.unresolved_imports
    ] == [
        (
            "ns",
            UnresolvedReason.NAMESPACE_BASE_UNMODELLED,
            ("fact-from-ns", "fact-import-ns"),
        ),
        (
            "ns.missing",
            UnresolvedReason.MISSING_INTERNAL_TARGET,
            ("fact-missing",),
        ),
    ]


def test_relative_imports_use_package_context_and_report_escapes() -> None:
    modules = (
        _module("top"),
        _module("pkg", package=True),
        _module("pkg.mod"),
        _module("pkg.service"),
        _module("pkg.other"),
        _module("pkg.sub", package=True),
        _module("pkg.sub.mod"),
    )
    facts = (
        _fact(
            "fact-sibling",
            "pkg.mod",
            syntax=ImportSyntax.IMPORT_FROM,
            base="service",
            name="Thing",
            level=1,
        ),
        _fact(
            "fact-parent",
            "pkg.sub.mod",
            syntax=ImportSyntax.IMPORT_FROM,
            base="other",
            name="Thing",
            level=2,
        ),
        _fact(
            "fact-package-context",
            "pkg.sub",
            syntax=ImportSyntax.IMPORT_FROM,
            base="mod",
            name="Thing",
            level=1,
        ),
        _fact(
            "fact-top",
            "top",
            syntax=ImportSyntax.IMPORT_FROM,
            base="x",
            name="Thing",
            level=1,
        ),
        _fact(
            "fact-escape",
            "pkg.mod",
            syntax=ImportSyntax.IMPORT_FROM,
            base="outside",
            name="Thing",
            level=2,
        ),
    )

    result = resolve_imports(facts, modules, ("pkg.sub",))

    assert _dependency_map(result) == {
        ("pkg.mod", "pkg.service"): (("fact-sibling", ResolutionKind.EXACT_BASE),),
        ("pkg.sub", "pkg.sub.mod"): (
            ("fact-package-context", ResolutionKind.EXACT_BASE),
        ),
        ("pkg.sub.mod", "pkg.other"): (("fact-parent", ResolutionKind.EXACT_BASE),),
    }
    assert [
        (item.source, item.requested, item.reason) for item in result.unresolved_imports
    ] == [
        ("pkg.mod", "..outside", UnresolvedReason.RELATIVE_ESCAPE),
        ("top", ".x", UnresolvedReason.RELATIVE_ESCAPE),
    ]


def test_absent_import_classification_has_declared_precedence() -> None:
    modules = (
        _module("app"),
        _module("internal", package=True),
        _module("sys"),
        _module("os.child"),
    )
    facts = (
        _fact("fact-exact-stdlib-name", "app", base="sys"),
        _fact("fact-namespace-stdlib-name", "app", base="os"),
        _fact("fact-missing", "app", base="internal.missing"),
        _fact("fact-stdlib", "app", base="pathlib.missing"),
        _fact("fact-unknown-z", "app", base="some_unknown_distribution"),
        _fact("fact-unknown-a", "app", base="some_unknown_distribution"),
    )

    result = resolve_imports(facts, modules, ("os",))

    assert ("app", "sys") in _dependency_map(result)
    assert [
        (item.requested, item.classification, item.fact_ids)
        for item in result.external_imports
    ] == [
        (
            "pathlib.missing",
            ExternalClassification.STDLIB,
            ("fact-stdlib",),
        ),
        (
            "some_unknown_distribution",
            ExternalClassification.EXTERNAL_UNKNOWN,
            ("fact-unknown-a", "fact-unknown-z"),
        ),
    ]
    assert [(item.requested, item.reason) for item in result.unresolved_imports] == [
        ("internal.missing", UnresolvedReason.MISSING_INTERNAL_TARGET),
        ("os", UnresolvedReason.NAMESPACE_BASE_UNMODELLED),
    ]


def test_external_from_import_classifies_only_the_definite_base() -> None:
    modules = (_module("app"),)
    facts = (
        _fact(
            "fact-os",
            "app",
            syntax=ImportSyntax.IMPORT_FROM,
            base="os",
            name="path",
        ),
        _fact(
            "fact-unknown",
            "app",
            syntax=ImportSyntax.IMPORT_FROM,
            base="not_a_real_top_level_name",
            name="thing",
        ),
    )

    result = resolve_imports(facts, modules, ())

    assert result.dependencies == ()
    assert [
        (item.requested, item.classification) for item in result.external_imports
    ] == [
        ("not_a_real_top_level_name", ExternalClassification.EXTERNAL_UNKNOWN),
        ("os", ExternalClassification.STDLIB),
    ]
    assert result.unresolved_imports == ()
