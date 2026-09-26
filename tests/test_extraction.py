from __future__ import annotations

import ast
import hashlib
import json
import random
from pathlib import Path

import pytest

from pyarchgraph import analyse, extraction
from pyarchgraph.discovery import discover_modules
from pyarchgraph.extraction import AstImportFactSource
from pyarchgraph.model import ImportScope, ImportSyntax, Severity, SourceModule


def _module(
    module_id: str,
    path: str,
    *,
    is_package: bool = False,
) -> SourceModule:
    return SourceModule(
        id=module_id,
        path=path,
        is_package=is_package,
        parent_package=(
            module_id if is_package else module_id.rpartition(".")[0] or None
        ),
    )


def test_extracts_one_source_backed_fact_per_alias(tmp_path: Path) -> None:
    source = """import alpha.beta, gamma as g
from base import value as renamed, other
from .service import Thing
from .. import *
"""
    path = tmp_path / "pkg" / "mod.py"
    path.parent.mkdir()
    path.write_text(source, encoding="utf-8")

    result = AstImportFactSource().collect(
        tmp_path, (_module("pkg.mod", "pkg/mod.py"),)
    )

    assert result.diagnostics == ()
    assert len(result.facts) == 6
    assert [fact.alias_index for fact in result.facts] == [0, 1, 0, 1, 0, 0]
    assert [fact.syntax for fact in result.facts] == [
        ImportSyntax.IMPORT,
        ImportSyntax.IMPORT,
        ImportSyntax.IMPORT_FROM,
        ImportSyntax.IMPORT_FROM,
        ImportSyntax.IMPORT_FROM,
        ImportSyntax.IMPORT_FROM,
    ]
    assert [
        (
            fact.base_module,
            fact.imported_name,
            fact.as_name,
            fact.bound_name,
            fact.relative_level,
        )
        for fact in result.facts
    ] == [
        ("alpha.beta", None, None, "alpha", 0),
        ("gamma", None, "g", "g", 0),
        ("base", "value", "renamed", "renamed", 0),
        ("base", "other", None, "other", 0),
        ("service", "Thing", None, "Thing", 1),
        (None, "*", None, "*", 2),
    ]
    assert result.facts[0].source_segment == "import alpha.beta, gamma as g"
    assert result.facts[0].line == 1
    assert result.facts[0].column == 0
    assert result.facts[0].end_line == 1
    assert result.facts[0].end_column == len("import alpha.beta, gamma as g")
    assert all(fact.scope is ImportScope.MODULE for fact in result.facts)


def test_unicode_fact_and_dynamic_diagnostic_columns_count_characters(
    tmp_path: Path,
) -> None:
    source = (
        "é = '\u2028🦉'; import bêta\n"
        "é = 1; __import__('bêta')\n"
        "é = 1; __import__(target)\n"
        "__import__(\n    '插件')\n"
    )
    (tmp_path / "mod.py").write_text(source, encoding="utf-8")
    result = AstImportFactSource().collect(tmp_path, (_module("mod", "mod.py"),))
    lines = source.split("\n")

    assert [(fact.line, fact.column) for fact in result.facts] == [
        (1, lines[0].index("import")),
        (2, lines[1].index("__import__")),
        (4, 0),
    ]
    for fact in result.facts:
        assert fact.end_line is not None and fact.end_column is not None
        assert fact.end_column == len(lines[fact.end_line - 1])
        selected = lines[fact.line - 1 : fact.end_line]
        selected[-1] = selected[-1][: fact.end_column]
        selected[0] = selected[0][fact.column :]
        assert "\n".join(selected) == fact.source_segment
    assert [(item.line, item.column) for item in result.diagnostics] == [
        (2, lines[1].index("__import__")),
        (3, lines[2].index("__import__")),
        (4, 0),
    ]


def test_unicode_columns_are_one_based_in_public_findings(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        "é = 1; import b\né = 1; __import__('b')\né = 1; __import__(target)\n",
        encoding="utf-8",
    )
    (tmp_path / "b.py").write_text("", encoding="utf-8")
    report = analyse(tmp_path, forbidden_dependencies=(("a", "b"),))
    boundary, *dynamic = report.findings
    assert boundary.kind == "forbidden_dependency"
    assert [(item.line, item.column) for item in boundary.witness[0].evidence] == [
        (1, 8),
        (2, 8),
    ]
    assert [(item.kind, item.requested) for item in dynamic] == [
        ("dynamic_import", "b"),
        ("dynamic_import", None),
    ]
    assert [(item.evidence[0].line, item.evidence[0].column) for item in dynamic] == [
        (2, 8),
        (3, 8),
    ]


def test_unicode_syntax_error_columns_are_not_converted_twice(tmp_path: Path) -> None:
    source = "é = 1; import\n"
    (tmp_path / "mod.py").write_text(source, encoding="utf-8")
    with pytest.raises(SyntaxError) as caught:
        ast.parse(source)
    result = AstImportFactSource().collect(tmp_path, (_module("mod", "mod.py"),))

    (diagnostic,) = result.diagnostics
    assert diagnostic.code == "source_syntax_error"
    assert caught.value.offset is not None
    assert diagnostic.column == caught.value.offset - 1


def test_scope_and_exact_type_checking_guard_semantics(tmp_path: Path) -> None:
    source = """if LATE:
    import before_binding
from typing import TYPE_CHECKING as TC
import typing as t
if TC:
    import direct
    if anything:
        import nested
else:
    import runtime_else
if t.TYPE_CHECKING:
    from . import typed_leaf
if TC and anything:
    import unsupported_boolean
def function():
    if TC:
        import local_typed
    import local_runtime
from typing import TYPE_CHECKING as LATE
"""
    path = tmp_path / "pkg" / "mod.py"
    path.parent.mkdir()
    path.write_text(source, encoding="utf-8")

    result = AstImportFactSource().collect(
        tmp_path, (_module("pkg.mod", "pkg/mod.py"),)
    )
    facts = {
        fact.base_module or fact.imported_name or "": fact for fact in result.facts
    }

    assert facts["before_binding"].type_only is False
    assert facts["direct"].type_only is True
    assert facts["nested"].type_only is True
    assert facts["runtime_else"].type_only is False
    assert facts["typed_leaf"].type_only is True
    assert facts["unsupported_boolean"].type_only is False
    assert facts["local_typed"].type_only is True
    assert facts["local_typed"].scope is ImportScope.LOCAL
    assert facts["local_runtime"].type_only is False
    assert facts["local_runtime"].scope is ImportScope.LOCAL


def test_dynamic_literals_keep_source_evidence_and_nonliteral_calls_warn(
    tmp_path: Path,
) -> None:
    source = """import importlib
__import__("literal")
__import__(computed)
importlib.import_module("direct")
load = importlib.import_module
load("assigned_alias")
other.import_module("also-ignored")
"""
    path = tmp_path / "mod.py"
    path.write_text(source, encoding="utf-8")

    result = AstImportFactSource().collect(tmp_path, (_module("mod", "mod.py"),))

    assert [(item.code, item.line, item.column) for item in result.diagnostics] == [
        ("dynamic_import_ignored", 2, 0),
        ("dynamic_import_ignored", 3, 0),
        ("dynamic_import_ignored", 4, 0),
        ("dynamic_import_ignored", 6, 0),
    ]
    assert all(item.severity is Severity.WARNING for item in result.diagnostics)
    assert len(result.facts) == 4
    assert result.facts[0].base_module == "importlib"
    assert [fact.base_module for fact in result.facts[1:]] == [
        "literal",
        "direct",
        "assigned_alias",
    ]
    assert all(fact.syntax is ImportSyntax.DYNAMIC_IMPORT for fact in result.facts[1:])
    assert result.facts[1].source_segment == '__import__("literal")'
    assert result.facts[1].line == 2
    assert result.facts[1].bound_name == ""


def test_importlib_aliases_and_relative_literal_targets(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(
        """import importlib as loader
from importlib import import_module as load
loader.import_module("pkg.first")
load(name="pkg.second")
load(".child", package="pkg")
load("..sibling", "pkg.child")
load(variable)
load(".unknown", package=variable)
__import__("relative", globals(), locals(), (), 1)
""",
        encoding="utf-8",
    )

    result = AstImportFactSource().collect(tmp_path, (_module("mod", "mod.py"),))

    dynamic = [
        fact for fact in result.facts if fact.syntax is ImportSyntax.DYNAMIC_IMPORT
    ]
    assert [fact.base_module for fact in dynamic] == [
        "pkg.first",
        "pkg.second",
        "pkg.child",
        "pkg.sibling",
    ]
    assert [item.line for item in result.diagnostics] == list(range(3, 10))


def test_dynamic_aliases_respect_parameters_rebinding_and_local_names(
    tmp_path: Path,
) -> None:
    (tmp_path / "mod.py").write_text(
        """import importlib as loader
from importlib import import_module as load
def parameter(load, loader, __import__):
    load("not-an-import")
    loader.import_module("not-an-import")
    __import__("not-an-import")
def local_binding():
    load("unbound-local")
    load = custom_loader
def local_alias():
    from importlib import import_module as local_load
    local_load("pkg.local")
load("pkg.module")
load = custom_loader
load("not-an-import")
loader = custom_loader
loader.import_module("not-an-import")
__import__ = custom_loader
__import__("not-an-import")
""",
        encoding="utf-8",
    )

    result = AstImportFactSource().collect(tmp_path, (_module("mod", "mod.py"),))

    assert [item.line for item in result.diagnostics] == [12, 13]
    dynamic = [
        fact for fact in result.facts if fact.syntax is ImportSyntax.DYNAMIC_IMPORT
    ]
    assert [(fact.base_module, fact.scope) for fact in dynamic] == [
        ("pkg.local", ImportScope.LOCAL),
        ("pkg.module", ImportScope.MODULE),
    ]


def test_dynamic_aliases_keep_typing_only_context(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(
        """from typing import TYPE_CHECKING
from importlib import import_module as load
if TYPE_CHECKING:
    load("pkg.typing_only")
""",
        encoding="utf-8",
    )
    result = AstImportFactSource().collect(tmp_path, (_module("mod", "mod.py"),))
    assert result.facts[-1].type_only is True
    assert result.facts[-1].syntax is ImportSyntax.DYNAMIC_IMPORT


def test_dynamic_aliases_respect_lambda_comprehension_and_class_scopes(
    tmp_path: Path,
) -> None:
    (tmp_path / "mod.py").write_text(
        """from importlib import import_module as load
lambda load: load("shadowed")
[load("shadowed") for load in callbacks]
load("pkg.after_comprehension")
class Example:
    load = custom_loader
    def method(self):
        load("pkg.from_module")
try:
    pass
except Exception as load:
    load("shadowed")
""",
        encoding="utf-8",
    )
    result = AstImportFactSource().collect(tmp_path, (_module("mod", "mod.py"),))
    dynamic = [
        fact for fact in result.facts if fact.syntax is ImportSyntax.DYNAMIC_IMPORT
    ]
    assert [fact.base_module for fact in dynamic] == [
        "pkg.after_comprehension",
        "pkg.from_module",
    ]
    assert [item.line for item in result.diagnostics] == [4, 8]


def test_assignment_aliases_are_followed_until_rebound(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text(
        """import importlib.util
load = importlib.import_module
copied: object = load
copied("pkg.first")
load = load("pkg.second")
load("not_a_loader")
copy_builtin = __import__
copy_builtin("pkg.third")
""",
        encoding="utf-8",
    )
    result = AstImportFactSource().collect(tmp_path, (_module("mod", "mod.py"),))
    assert [item.line for item in result.diagnostics] == [4, 5, 8]
    dynamic = [
        fact for fact in result.facts if fact.syntax is ImportSyntax.DYNAMIC_IMPORT
    ]
    assert [fact.base_module for fact in dynamic] == [
        "pkg.first",
        "pkg.second",
        "pkg.third",
    ]


@pytest.mark.parametrize(
    "source",
    [
        'from importlib import import_module as load\nfor load in load("b"):\n    pass\n',
        (
            "import importlib as loader\nclass C:\n    loader = object()\n"
            '    result = [loader.import_module("b") for _ in [1]]\n'
        ),
        'from importlib import import_module as load\nclass load:\n    result = load("b")\n',
    ],
)
def test_dynamic_aliases_use_runtime_binding_order_and_class_comprehension_scope(
    tmp_path: Path, source: str
) -> None:
    (tmp_path / "a.py").write_text(source, encoding="utf-8")
    (tmp_path / "b.py").write_text("import a\n", encoding="utf-8")

    result = AstImportFactSource().collect(tmp_path, discover_modules(tmp_path).modules)

    dynamic = [
        fact for fact in result.facts if fact.syntax is ImportSyntax.DYNAMIC_IMPORT
    ]
    assert [fact.base_module for fact in dynamic] == ["b"]


@pytest.mark.parametrize(
    "source",
    [
        "from typing import TYPE_CHECKING as TC\nTC = True\nif TC:\n    import b\n",
        "import typing as t\nt = object()\nif t.TYPE_CHECKING:\n    import b\n",
        "from typing import TYPE_CHECKING as TC\ndef f(TC):\n    if TC:\n        import b\n",
    ],
)
def test_rebound_typing_guards_are_not_classified_as_type_only(
    tmp_path: Path, source: str
) -> None:
    (tmp_path / "a.py").write_text(source, encoding="utf-8")
    (tmp_path / "b.py").write_text("import a\n", encoding="utf-8")

    result = AstImportFactSource().collect(tmp_path, discover_modules(tmp_path).modules)

    assert not next(fact for fact in result.facts if fact.base_module == "b").type_only


def test_simple_assigned_typing_alias_keeps_type_only_classification(
    tmp_path: Path,
) -> None:
    (tmp_path / "a.py").write_text(
        "from typing import TYPE_CHECKING\nTC = TYPE_CHECKING\nif TC:\n    import b\n",
        encoding="utf-8",
    )
    (tmp_path / "b.py").write_text("import a\n", encoding="utf-8")

    result = AstImportFactSource().collect(tmp_path, discover_modules(tmp_path).modules)

    assert next(fact for fact in result.facts if fact.base_module == "b").type_only


def test_read_decode_and_parse_failures_are_stable_and_do_not_stop_collection(
    tmp_path: Path,
) -> None:
    (tmp_path / "good.py").write_text(
        "raise RuntimeError('must not execute')\nimport target\n",
        encoding="utf-8",
    )
    (tmp_path / "bad_decode.py").write_bytes(b"# coding: utf-8\n\xff\n")
    (tmp_path / "bad_syntax.py").write_text("from broken import\n", encoding="utf-8")

    modules = (
        _module("missing", "missing.py"),
        _module("bad_syntax", "bad_syntax.py"),
        _module("good", "good.py"),
        _module("bad_decode", "bad_decode.py"),
    )
    result = AstImportFactSource().collect(tmp_path, modules)

    assert [(item.code, item.path) for item in result.diagnostics] == [
        ("source_decode_error", "bad_decode.py"),
        ("source_read_error", "missing.py"),
        ("source_syntax_error", "bad_syntax.py"),
    ]
    assert all(item.severity is Severity.ERROR for item in result.diagnostics)
    assert all(str(tmp_path) not in item.message for item in result.diagnostics)
    assert [(fact.source, fact.base_module) for fact in result.facts] == [
        ("good", "target")
    ]


def test_fact_ids_and_order_are_deterministic(tmp_path: Path) -> None:
    (tmp_path / "b.py").write_text("import z\n", encoding="utf-8")
    (tmp_path / "a.py").write_text("import y\n", encoding="utf-8")
    a = _module("a", "a.py")
    b = _module("b", "b.py")
    collector = AstImportFactSource()

    forward = collector.collect(tmp_path, (a, b))
    reverse = collector.collect(tmp_path, (b, a))

    assert forward == reverse
    assert [fact.source for fact in forward.facts] == ["a", "b"]
    assert all(
        fact.id.startswith("fact-") and len(fact.id) == len("fact-") + 12
        for fact in forward.facts
    )

    first = forward.facts[0]
    identity = (
        first.source,
        first.path,
        first.line,
        first.column,
        first.end_line,
        first.end_column,
        first.alias_index,
        first.syntax.value,
        first.base_module,
        first.imported_name,
        first.as_name,
        first.bound_name,
        first.relative_level,
        first.scope.value,
        first.type_only,
        first.source_segment,
    )
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    assert first.id == f"fact-{hashlib.sha256(encoded).hexdigest()[:12]}"


def test_fact_id_prefix_collisions_are_extended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "mod.py").write_text("import a, b, c\n", encoding="utf-8")
    digests = {
        "a": "123456789abc0" + "0" * 51,
        "b": "123456789abc1" + "1" * 51,
        "c": "fedcba9876542" + "2" * 51,
    }
    monkeypatch.setattr(
        extraction,
        "_fact_digest",
        lambda fact: digests[fact.base_module or ""],
    )

    result = AstImportFactSource().collect(tmp_path, (_module("mod", "mod.py"),))

    assert [fact.id for fact in result.facts] == [
        "fact-123456789abc0",
        "fact-123456789abc1",
        "fact-fedcba987654",
    ]


def test_full_fact_digest_collision_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "mod.py").write_text("import a, b\n", encoding="utf-8")
    monkeypatch.setattr(extraction, "_fact_digest", lambda fact: "0" * 64)

    with pytest.raises(ValueError, match="unique full SHA-256"):
        AstImportFactSource().collect(tmp_path, (_module("mod", "mod.py"),))


def test_prefix_lengths_match_pairwise_reference_for_collisions_and_duplicates() -> (
    None
):
    rng = random.Random(420)
    digests = [hashlib.sha256(str(index).encode()).hexdigest() for index in range(150)]
    digests.extend(["a" * 12 + "0" * 52, "a" * 12 + "1" * 52, "a" * 63 + "b", "a" * 64])
    digests.extend(digests[:3])
    rng.shuffle(digests)

    expected = []
    for digest in digests:
        length = 12
        while any(
            other != digest and other.startswith(digest[:length]) for other in digests
        ):
            length += 1
        expected.append(length)

    assert extraction._minimum_unique_prefix_lengths(tuple(digests)) == tuple(expected)
    assert extraction._minimum_unique_prefix_lengths(()) == ()
    assert extraction._minimum_unique_prefix_lengths(("f" * 64,)) == (12,)


def test_raw_collection_has_same_evidence_without_assigning_ids(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("import a, b\n", encoding="utf-8")
    collector = AstImportFactSource()
    modules = (_module("mod", "mod.py"),)

    raw = collector.collect_uncanonicalised(tmp_path, modules)
    canonical = collector.collect(tmp_path, modules)

    assert [fact.id for fact in raw.facts] == ["", ""]
    assert extraction.canonicalise_fact_ids(raw.facts) == canonical.facts
    assert raw.diagnostics == canonical.diagnostics
