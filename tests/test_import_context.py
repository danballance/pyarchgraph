"""Context labels remain conservative and independent of target resolution."""

from pathlib import Path

import pytest

from benchmarks.reference_adapter import ReferenceFactSource
from pyarchgraph.composition import ApplicationFactory
from pyarchgraph.domain.model import SourceModule


def _contexts(tmp_path: Path, source: str, *, package: bool = False):
    (tmp_path / "source.py").write_text(source, encoding="utf-8")
    result = (
        ApplicationFactory()
        .create_fact_source()
        .collect(tmp_path, (SourceModule("opaque-source", "source.py", package, None),))
    )
    assert result == ReferenceFactSource().collect(
        tmp_path, (SourceModule("opaque-source", "source.py", package, None),)
    )
    assert not result.diagnostics
    return {fact.base_module: fact.context for fact in result.facts}


@pytest.mark.parametrize(
    "declaration,guard",
    [
        ("from typing import TYPE_CHECKING", "TYPE_CHECKING"),
        ("from typing import TYPE_CHECKING as TC", "TC"),
        ("import typing", "typing.TYPE_CHECKING"),
        ("import typing as t", "t.TYPE_CHECKING"),
    ],
)
def test_typing_aliases_only_mark_positive_body(tmp_path: Path, declaration, guard):
    contexts = _contexts(
        tmp_path,
        f"{declaration}\nif {guard}:\n    import body\nelse:\n    import other\n",
    )
    assert contexts["body"].typing_only
    assert not contexts["other"].typing_only
    assert contexts["body"].conditional and contexts["other"].conditional


@pytest.mark.parametrize(
    "change",
    [
        "TC = True",
        "del TC",
        "def TC(): pass",
        "class TC: pass",
        "def f(TC): pass",
        "for TC in []: pass",
        "import other as TC",
        "from other import TC",
        "from other import *",
        "try:\n    pass\nexcept Exception as TC:\n    pass",
        "match value:\n    case {'x': TC}: pass",
        "match value:\n    case [*TC]: pass",
        "match value:\n    case {**TC}: pass",
        "(TC := True)",
        "value = [TC for TC in values]",
    ],
)
def test_observed_rebinding_disables_alias_throughout_file(tmp_path: Path, change):
    contexts = _contexts(
        tmp_path,
        "from typing import TYPE_CHECKING as TC\nif TC:\n    import target\n"
        + change
        + "\n",
    )
    assert not contexts["target"].typing_only


@pytest.mark.parametrize(
    "change",
    ["t = None", "t.TYPE_CHECKING = True", "del t.TYPE_CHECKING", "def f(t): pass"],
)
def test_qualified_alias_rebinding_is_not_typing_only(tmp_path: Path, change):
    contexts = _contexts(
        tmp_path,
        "import typing as t\nif t.TYPE_CHECKING:\n    import target\n" + change + "\n",
    )
    assert not contexts["target"].typing_only


@pytest.mark.parametrize(
    "guard", ["not TC", "TC and flag", "TC or flag", "TC == True", "custom"]
)
def test_unsupported_predicates_remain_ordinary_conditions(tmp_path: Path, guard):
    contexts = _contexts(
        tmp_path,
        f"from typing import TYPE_CHECKING as TC\nif {guard}:\n    import target\n",
    )
    assert contexts["target"].conditional
    assert not contexts["target"].typing_only


def test_scoped_or_conditional_aliases_do_not_establish_module_bindings(tmp_path: Path):
    contexts = _contexts(
        tmp_path,
        "def f():\n    from typing import TYPE_CHECKING as TC\nif TC:\n    import target\n",
    )
    assert not contexts["target"].typing_only
    contexts = _contexts(
        tmp_path,
        "if flag:\n    import typing as t\nif t.TYPE_CHECKING:\n    import target\n",
    )
    assert not contexts["target"].typing_only


def test_scope_and_enclosing_function_are_distinct(tmp_path: Path):
    contexts = _contexts(
        tmp_path,
        """import module_site
class C:
    import class_site
    def method(self):
        import method_site
        class Nested:
            import nested_class
async def async_function():
    import async_site
""",
        package=True,
    )
    assert contexts["module_site"].scope == "module"
    assert contexts["class_site"].scope == "class"
    assert not contexts["class_site"].in_function
    assert contexts["method_site"].scope == "function"
    assert contexts["async_site"].in_function
    assert contexts["nested_class"].scope == "class"
    assert contexts["nested_class"].in_function
    assert all(context.package_initializer for context in contexts.values())


def test_nested_guards_and_exception_handlers_preserve_context(tmp_path: Path):
    contexts = _contexts(
        tmp_path,
        """from typing import TYPE_CHECKING
if TYPE_CHECKING:
    def deferred():
        if flag:
            import guarded
        else:
            import alternative
try:
    import attempted
except ImportError:
    import fallback
finally:
    import cleanup
""",
    )
    for name in ("guarded", "alternative"):
        assert contexts[name].typing_only and contexts[name].in_function
    assert contexts["attempted"].conditional
    assert contexts["fallback"].exception_handler
    assert not contexts["cleanup"].exception_handler


def test_context_changes_fact_identity(tmp_path: Path):
    path = tmp_path / "source.py"
    module = SourceModule("opaque", "source.py", False, None)
    path.write_text(
        "from typing import TYPE_CHECKING as TC\nif TC:\n    import target\n"
    )
    before = (
        ApplicationFactory().create_fact_source().collect(tmp_path, (module,)).facts[-1]
    )
    path.write_text(
        "from otherx import TYPE_CHECKING as TC\nif TC:\n    import target\n"
    )
    after = (
        ApplicationFactory().create_fact_source().collect(tmp_path, (module,)).facts[-1]
    )
    assert before.line == after.line and before.column == after.column
    assert before.source_segment == after.source_segment
    assert before.context.typing_only and not after.context.typing_only
    assert before.id != after.id
