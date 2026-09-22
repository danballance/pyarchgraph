"""Extract deterministic, source-backed import facts from Python ASTs."""

from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tokenize
import stat
from typing import Iterable

from pyarchgraph.model import (
    Diagnostic,
    FactCollection,
    ImportFact,
    ImportScope,
    ImportSyntax,
    Severity,
    SourceModule,
)


_FACT_ID_PREFIX_LENGTH = 12


def _nullable_string(value: str | None) -> str:
    return "" if value is None else value


def _nullable_integer(value: int | None) -> int:
    return -1 if value is None else value


def _fact_sort_key(fact: ImportFact) -> tuple[object, ...]:
    """Return the complete, normalized source-site ordering key."""

    return (
        fact.source,
        fact.path,
        fact.line,
        fact.column,
        _nullable_integer(fact.end_line),
        _nullable_integer(fact.end_column),
        fact.alias_index,
        fact.syntax.value,
        _nullable_string(fact.base_module),
        _nullable_string(fact.imported_name),
        _nullable_string(fact.as_name),
        fact.bound_name,
        fact.relative_level,
        fact.scope.value,
        fact.type_only,
        _nullable_string(fact.source_segment),
    )


def _fact_identity(fact: ImportFact) -> tuple[object, ...]:
    """Return the canonical, unnormalised tuple used to derive a fact ID."""

    return (
        fact.source,
        fact.path,
        fact.line,
        fact.column,
        fact.end_line,
        fact.end_column,
        fact.alias_index,
        fact.syntax.value,
        fact.base_module,
        fact.imported_name,
        fact.as_name,
        fact.bound_name,
        fact.relative_level,
        fact.scope.value,
        fact.type_only,
        fact.source_segment,
    )


def _fact_digest(fact: ImportFact) -> str:
    encoded_identity = json.dumps(
        _fact_identity(fact),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded_identity).hexdigest()


def _minimum_unique_prefix_lengths(digests: tuple[str, ...]) -> tuple[int, ...]:
    """Choose the same collision-safe prefixes using sorted neighbours.

    A digest's longest shared prefix must occur with one of its lexicographic
    neighbours. Sorting distinct hashes therefore avoids an all-pairs scan;
    duplicate full hashes retain the helper's historical equal-hash behaviour
    (the public canonicaliser rejects them separately).
    """

    ordered = sorted(set(digests))
    lengths = dict.fromkeys(ordered, _FACT_ID_PREFIX_LENGTH)
    for left, right in zip(ordered, ordered[1:]):
        common = 0
        for left_char, right_char in zip(left, right):
            if left_char != right_char:
                break
            common += 1
        length = max(_FACT_ID_PREFIX_LENGTH, common + 1)
        lengths[left] = max(lengths[left], length)
        lengths[right] = max(lengths[right], length)
    return tuple(lengths[digest] for digest in digests)


def _assign_fact_ids(facts: Iterable[ImportFact]) -> tuple[ImportFact, ...]:
    ordered = tuple(sorted(facts, key=_fact_sort_key))
    digests = tuple(_fact_digest(fact) for fact in ordered)
    if len(set(digests)) != len(digests):
        raise ValueError(
            "import facts must be unique and produce unique full SHA-256 digests"
        )
    lengths = _minimum_unique_prefix_lengths(digests)
    return tuple(
        replace(fact, id=f"fact-{digest[:length]}")
        for fact, digest, length in zip(ordered, digests, lengths, strict=True)
    )


def canonicalise_fact_ids(facts: Iterable[ImportFact]) -> tuple[ImportFact, ...]:
    """Sort facts and assign stable, collision-safe content-derived IDs."""

    return _assign_fact_ids(facts)


def _diagnostic_sort_key(diagnostic: Diagnostic) -> tuple[object, ...]:
    return (
        diagnostic.severity.value,
        diagnostic.code,
        diagnostic.path or "",
        _nullable_integer(diagnostic.line),
        _nullable_integer(diagnostic.column),
        diagnostic.message,
    )


def _syntax_error_column(error: SyntaxError) -> int | None:
    if error.offset is None:
        return None
    return max(error.offset - 1, 0)


# Unknown is an explicit alternative: unions retain possible loaders, while only
# singleton typing aliases can justify excluding an import from the graph.
_UNKNOWN = frozenset({"unknown"})
_Aliases = dict[str, frozenset[str]]


def _merge_aliases(*paths: _Aliases) -> _Aliases:
    return {
        name: frozenset().union(*(path.get(name, _UNKNOWN) for path in paths))
        for name in set().union(*paths)
    }


def _alias_values(expression: ast.expr, aliases: _Aliases) -> frozenset[str]:
    if isinstance(expression, ast.Name):
        return aliases.get(expression.id, _UNKNOWN)
    if isinstance(expression, ast.Attribute) and isinstance(expression.value, ast.Name):
        return frozenset(
            "import_module"
            if kind == "importlib" and expression.attr == "import_module"
            else "type_checking"
            if kind == "typing" and expression.attr == "TYPE_CHECKING"
            else "unknown"
            for kind in aliases.get(expression.value.id, _UNKNOWN)
        )
    return _UNKNOWN


def _import_alias(node: ast.Import | ast.ImportFrom, alias: ast.alias) -> str:
    if isinstance(node, ast.Import):
        if alias.name == "importlib" or (
            alias.name.startswith("importlib.") and alias.asname is None
        ):
            return "importlib"
        if alias.name == "typing":
            return "typing"
    elif node.level == 0:
        if node.module == "importlib" and alias.name == "import_module":
            return "import_module"
        if node.module == "typing" and alias.name == "TYPE_CHECKING":
            return "type_checking"
    return "unknown"


def _bound_names(node: ast.AST) -> tuple[str, ...]:
    """Binding fields shared by lexical discovery and sequential invalidation."""
    if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
        return (node.id,)
    if isinstance(node, ast.arg):
        return (node.arg,)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return (node.name,)
    if isinstance(node, ast.Import):
        return tuple(
            alias.asname or alias.name.partition(".")[0] for alias in node.names
        )
    if isinstance(node, ast.ImportFrom):
        return tuple(alias.asname or alias.name for alias in node.names)
    if isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
        return (node.name,) if node.name is not None else ()
    if isinstance(node, ast.MatchMapping):
        return (node.rest,) if node.rest is not None else ()
    # These AST classes first exist on Python 3.12; keep 3.11 importable.
    if type(node).__name__ in {"TypeVar", "ParamSpec", "TypeVarTuple", "TypeAlias"}:
        return (node.name,) if isinstance(node.name, str) else _bound_names(node.name)
    return ()


def _definition_expressions(node: ast.AST) -> Iterable[ast.AST]:
    """Expressions executed in the containing scope when a definition is made."""
    yield from getattr(node, "decorator_list", ())
    if isinstance(node, ast.ClassDef):
        yield from node.bases
        yield from (keyword.value for keyword in node.keywords)
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        yield from node.args.defaults
        yield from (value for value in node.args.kw_defaults if value is not None)


def _arguments(node: ast.arguments) -> tuple[ast.arg, ...]:
    return (
        *node.posonlyargs,
        *node.args,
        *node.kwonlyargs,
        *((node.vararg,) if node.vararg else ()),
        *((node.kwarg,) if node.kwarg else ()),
    )


_COMPREHENSIONS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)
# TypeAlias first exists on Python 3.12; an empty tuple matches nothing on 3.11.
_TYPE_ALIAS = getattr(ast, "TypeAlias", ())


def _type_alias_expressions(node: ast.AST) -> Iterable[ast.AST]:
    """The independently lazy bounds, constraints, defaults, and alias value."""
    for parameter in node.type_params:
        yield from ast.iter_child_nodes(parameter)
    yield node.value


def _comprehension_expressions(node: ast.AST) -> Iterable[ast.expr]:
    for index, generator in enumerate(node.generators):
        if index:
            yield generator.iter
        yield from generator.ifs
    if isinstance(node, ast.DictComp):
        yield node.key
        yield node.value
    else:
        yield node.elt


class _LocalBindings(ast.NodeVisitor):
    """One binding inventory for lexical shadowing and deferred alias summaries.

    Summaries union every assignment that can affect a name; they deliberately
    do not assume a function runs at its definition site or after the last write.
    Comprehension iteration targets are local, but walrus targets belong here.
    """

    def __init__(self) -> None:
        self.writes: dict[str, list[str | ast.expr]] = {}
        self.globals: set[str] = set()
        self.nonlocals: set[str] = set()
        self.deleted_names: set[str] = set()
        self.definitions: list[ast.AST] = []

    @property
    def names(self) -> set[str]:
        return set(self.writes)

    def _record(self, name: str, value: str | ast.expr = "unknown") -> None:
        self.writes.setdefault(name, []).append(value)

    def generic_visit(self, node: ast.AST) -> None:
        for name in _bound_names(node):
            self._record(name)
            if isinstance(node, ast.ExceptHandler) or (
                isinstance(node, ast.Name) and isinstance(node.ctx, ast.Del)
            ):
                self.deleted_names.add(name)
        super().generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for name, alias in zip(_bound_names(node), node.names):
            self._record(name, _import_alias(node, alias))

    visit_ImportFrom = visit_Import

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        self.visit(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                self._record(target.id, node.value)
            else:
                self.visit(target)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        if node.value is not None:
            self.visit(node.value)
        if isinstance(node.target, ast.Name):
            self._record(node.target.id, node.value or "unknown")
        else:
            self.visit(node.target)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:  # noqa: N802
        self.visit(node.value)
        self._record(node.target.id, node.value)

    def visit_TypeAlias(self, node: ast.AST) -> None:  # noqa: N802
        # Only the alias object binds here. Its parameters and expression
        # scopes must not become locals of the containing function or module.
        for name in _bound_names(node):
            self._record(name)
        self.definitions.append(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.definitions.append(node)
        self._record(node.name)
        for expression in _definition_expressions(node):
            self.visit(expression)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            annotations = _LocalBindings()
            for argument in _arguments(node.args):
                if argument.annotation is not None:
                    annotations.visit(argument.annotation)
            if node.returns is not None:
                annotations.visit(node.returns)
            self.definitions.extend(annotations.definitions)

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        self.definitions.append(node)
        for expression in _definition_expressions(node):
            self.visit(expression)

    def visit_Global(self, node: ast.Global) -> None:  # noqa: N802
        self.globals.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:  # noqa: N802
        self.nonlocals.update(node.names)

    def _visit_comprehension(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ) -> None:
        self.visit(node.generators[0].iter)
        self.definitions.append(node)
        # Only assignment expressions escape to this lexical scope. Nested
        # lambdas belong to the comprehension and capture its iteration names.
        bindings = _LocalBindings()
        for expression in _comprehension_expressions(node):
            bindings.visit(expression)
        for name, writes in bindings.writes.items():
            self.writes.setdefault(name, []).extend(writes)

    visit_ListComp = _visit_comprehension
    visit_SetComp = _visit_comprehension
    visit_DictComp = _visit_comprehension
    visit_GeneratorExp = _visit_comprehension

    def summary(self, inherited: _Aliases) -> _Aliases:
        # A small monotone dataflow calculation supports chains of assigned
        # aliases, including forward declarations, without executing expressions.
        result = inherited.copy()
        for name in self.writes:
            result[name] = frozenset()
        while True:
            changed = False
            for name, writes in self.writes.items():
                values = frozenset().union(
                    *(
                        frozenset({value})
                        if isinstance(value, str)
                        else _alias_values(value, result)
                        for value in writes
                    )
                )
                combined = result[name] | values
                if combined != result[name]:
                    result[name] = combined
                    changed = True
            if not changed:
                break
        return {name: values or _UNKNOWN for name, values in result.items()}


class _ScopeIndex:
    """Resolve lexical owners once, including writes through global/nonlocal.

    A redirected write may execute between any two observations. Its possible
    alias values widen its owner's environment, never establish a typing guard.
    The finite alias domain makes the summary iteration monotone and bounded.
    """

    def __init__(self, tree: ast.Module, initial: _Aliases) -> None:
        self.bindings: dict[ast.AST, _LocalBindings] = {}
        self.parents: dict[ast.AST, ast.AST | None] = {}
        self.summaries: dict[ast.AST, _Aliases] = {}
        self.mutations: dict[ast.AST, _Aliases] = {}
        self.root = tree
        self.generator_writes: dict[ast.AST, set[str]] = {}
        self._add(tree, None)
        while True:
            changed = False
            for node, bindings in self.bindings.items():
                inherited = (
                    initial.copy()
                    if node is tree
                    else self.outer(node, annotation_scope=isinstance(node, _TYPE_ALIAS))
                )
                for name in bindings.globals:
                    inherited[name] = self.summaries.get(tree, {}).get(name, _UNKNOWN)
                summary = bindings.summary(inherited)
                if node is tree:
                    for name, values in initial.items():
                        summary[name] = summary.get(name, _UNKNOWN) | values
                for name in bindings.globals | bindings.nonlocals:
                    summary[name] = summary.get(name, _UNKNOWN) | inherited.get(
                        name, _UNKNOWN
                    )
                for name, values in self.mutations[node].items():
                    summary[name] = summary.get(name, _UNKNOWN) | values
                self.summaries[node] = summary
                redirected = (
                    bindings.globals
                    | bindings.nonlocals
                    | self.generator_writes.get(node, set())
                )
                for name in redirected:
                    if name not in bindings.writes:
                        continue
                    if name in self.generator_writes.get(node, set()):
                        owner = self.parents[node]
                        while isinstance(owner, _COMPREHENSIONS):
                            owner = self.parents[owner]
                        if name in self.bindings[owner].globals:
                            owner = tree
                        elif name in self.bindings[owner].nonlocals:
                            owner = self._nonlocal_owner(owner, name)
                    else:
                        owner = (
                            tree
                            if name in bindings.globals
                            else self._nonlocal_owner(node, name)
                        )
                    if owner is None:
                        continue
                    values = summary.get(name, _UNKNOWN)
                    previous = self.mutations[owner].get(name, frozenset())
                    if not values <= previous:
                        self.mutations[owner][name] = previous | values
                        changed = True
            if not changed:
                break

    def _add(self, node: ast.AST, parent: ast.AST | None) -> None:
        bindings = _LocalBindings()
        if isinstance(node, _TYPE_ALIAS):
            for expression in _type_alias_expressions(node):
                bindings.visit(expression)
        elif isinstance(node, _COMPREHENSIONS):
            for expression in _comprehension_expressions(node):
                bindings.visit(expression)
            if isinstance(node, ast.GeneratorExp):
                self.generator_writes[node] = bindings.names.copy()
            for generator in node.generators:
                bindings.visit(generator.target)
        else:
            body = [node.body] if isinstance(node, ast.Lambda) else node.body
            for statement in body:
                bindings.visit(statement)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            for argument in _arguments(node.args):
                bindings._record(argument.arg)
        for parameter in getattr(node, "type_params", ()):
            for name in _bound_names(parameter):
                bindings._record(name)
        self.bindings[node] = bindings
        self.parents[node] = parent
        self.mutations[node] = {}
        for child in bindings.definitions:
            self._add(child, node)

    def outer(self, node: ast.AST, *, annotation_scope: bool = False) -> _Aliases:
        parent = self.parents[node]
        if annotation_scope:
            # Lazy alias expressions can see the directly enclosing class,
            # including attributes bound after the alias statement.
            return self.summaries.get(parent, {}).copy()
        parameters: set[str] = set()
        while isinstance(parent, ast.ClassDef) or isinstance(parent, _TYPE_ALIAS):
            # Ordinary scopes nested inside an alias capture its parameters,
            # but must not inherit class attributes through its summary.
            for parameter in getattr(parent, "type_params", ()):
                parameters.update(_bound_names(parameter))
            parent = self.parents[parent]
        aliases = self.summaries.get(parent, {}).copy()
        for name in parameters:
            aliases[name] = _UNKNOWN
        return aliases

    def _nonlocal_owner(self, node: ast.AST, name: str) -> ast.AST | None:
        parent = self.parents[node]
        while parent is not None and parent is not self.root:
            bindings = self.bindings[parent]
            if (
                not isinstance(parent, ast.ClassDef)
                and name in bindings.names - bindings.globals - bindings.nonlocals
            ):
                return parent
            parent = self.parents[parent]
        return None


class _ImportVisitor(ast.NodeVisitor):
    """Collect source evidence using conservative path and lexical environments."""

    def __init__(self, module: SourceModule, source: str) -> None:
        self._module = module
        self._source = source
        self._local_depth = 0
        self._type_only = False
        self._aliases: _Aliases = {"__import__": frozenset({"__import__"})}
        self._global_aliases = self._aliases.copy()
        self._comprehension_writes: list[set[str]] = []
        self._mutations: _Aliases = {}
        self._class_outer_mutations: _Aliases | None = None
        self._class_outer_aliases: _Aliases | None = None
        self.facts: list[ImportFact] = []
        self.diagnostics: list[Diagnostic] = []

    @property
    def _scope(self) -> ImportScope:
        if self._local_depth:
            return ImportScope.LOCAL
        return ImportScope.MODULE

    def _make_fact(
        self,
        node: ast.Import | ast.ImportFrom,
        *,
        alias: ast.alias,
        alias_index: int,
        syntax: ImportSyntax,
        base_module: str | None,
        imported_name: str | None,
        bound_name: str,
        relative_level: int,
    ) -> ImportFact:
        return ImportFact(
            id="",
            source=self._module.id,
            path=self._module.path,
            line=node.lineno,
            column=node.col_offset,
            end_line=getattr(node, "end_lineno", None),
            end_column=getattr(node, "end_col_offset", None),
            alias_index=alias_index,
            syntax=syntax,
            source_segment=ast.get_source_segment(self._source, node),
            base_module=base_module,
            imported_name=imported_name,
            as_name=alias.asname,
            bound_name=bound_name,
            relative_level=relative_level,
            scope=self._scope,
            type_only=self._type_only,
        )

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias_index, alias in enumerate(node.names):
            bound_name = alias.asname or alias.name.partition(".")[0]
            self.facts.append(
                self._make_fact(
                    node,
                    alias=alias,
                    alias_index=alias_index,
                    syntax=ImportSyntax.IMPORT,
                    base_module=alias.name,
                    imported_name=None,
                    bound_name=bound_name,
                    relative_level=0,
                )
            )

            self._aliases[bound_name] = frozenset({_import_alias(node, alias)})

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        for alias_index, alias in enumerate(node.names):
            bound_name = alias.asname or alias.name
            self.facts.append(
                self._make_fact(
                    node,
                    alias=alias,
                    alias_index=alias_index,
                    syntax=ImportSyntax.IMPORT_FROM,
                    base_module=node.module,
                    imported_name=alias.name,
                    bound_name=bound_name,
                    relative_level=node.level,
                )
            )

            self._aliases[bound_name] = frozenset({_import_alias(node, alias)})

    def visit_Module(self, node: ast.Module) -> None:  # noqa: N802
        self._index = _ScopeIndex(node, self._aliases)
        self._global_aliases = self._index.summaries[node]
        self._mutations = self._index.mutations[node]
        for statement in node.body:
            self.visit(statement)

    def _path(self, statements: Iterable[ast.AST], incoming: _Aliases) -> _Aliases:
        self._aliases = incoming.copy()
        for statement in statements:
            self.visit(statement)
        return self._aliases

    def visit_If(self, node: ast.If) -> None:  # noqa: N802
        self.visit(node.test)
        guard = self._is_type_checking_guard(node.test)
        incoming = self._aliases.copy()
        inherited_type_only = self._type_only
        self._type_only |= guard
        body = self._path(node.body, incoming)
        self._type_only = inherited_type_only
        alternative = self._path(node.orelse, incoming)
        self._aliases = _merge_aliases(body, alternative)

    def visit_IfExp(self, node: ast.IfExp) -> None:  # noqa: N802
        self.visit(node.test)
        incoming = self._aliases.copy()
        body = self._path([node.body], incoming)
        alternative = self._path([node.orelse], incoming)
        self._aliases = _merge_aliases(body, alternative)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:  # noqa: N802
        exits = []
        for value in node.values:
            self.visit(value)
            exits.append(self._aliases.copy())
        self._aliases = _merge_aliases(*exits)

    def _is_type_checking_guard(self, test: ast.expr) -> bool:
        return self._known_alias(test) == frozenset({"type_checking"})

    def visit_Try(self, node: ast.Try) -> None:  # noqa: N802
        incoming = self._aliases.copy()
        # An exception can leave any prefix of the try suite applied.
        prefixes = [incoming]
        for statement in node.body:
            self.visit(statement)
            prefixes.append(self._aliases.copy())
        normal = self._path(node.orelse, self._aliases)
        bindings = _LocalBindings()
        for statement in node.body:
            bindings.visit(statement)
        exceptional = _merge_aliases(*prefixes, bindings.summary(incoming))
        exits = [normal, exceptional]
        for handler in node.handlers:
            handler_exit = self._path([handler], exceptional)
            exits.append(handler_exit)
            if isinstance(node, ast.TryStar):
                # Several except* suites can run for one exception group.
                exceptional = _merge_aliases(exceptional, handler_exit)
        self._aliases = self._path(node.finalbody, _merge_aliases(*exits))

    visit_TryStar = visit_Try

    def visit_Match(self, node: ast.Match) -> None:  # noqa: N802
        self.visit(node.subject)
        incoming = self._aliases.copy()
        exits = [incoming]  # Include no match; exhaustiveness is not inferred.
        for case in node.cases:
            self._aliases = incoming.copy()
            self.visit(case.pattern)
            if case.guard is not None:
                self.visit(case.guard)
            incoming = _merge_aliases(incoming, self._aliases)
            for statement in case.body:
                self.visit(statement)
            exits.append(self._aliases)
        self._aliases = _merge_aliases(*exits)

    def generic_visit(self, node: ast.AST) -> None:
        for name in _bound_names(node):
            self._aliases.pop(name, None)
        super().generic_visit(node)

    def _visit_type_parameters(self, node: ast.AST) -> None:
        for parameter in getattr(node, "type_params", ()):
            for name in _bound_names(parameter):
                self._aliases.pop(name, None)
            # Bounds/defaults use lazy annotation scopes that this collector
            # cannot establish. Fail explicitly rather than silently omitting
            # dynamic calls in those expressions.
            if any(
                isinstance(value, ast.AST) for _, value in ast.iter_fields(parameter)
            ):
                self.diagnostics.append(
                    Diagnostic(
                        severity=Severity.ERROR,
                        code="unsupported_annotation_scope",
                        message="Generic parameter bounds and defaults are not analysed.",
                        path=self._module.path,
                        line=parameter.lineno,
                        column=parameter.col_offset,
                    )
                )

    def visit_TypeAlias(self, node: ast.AST) -> None:  # noqa: N802
        for name in _bound_names(node):
            self._aliases.pop(name, None)
        inherited_aliases = self._aliases
        inherited_class_outer = self._class_outer_aliases
        inherited_class_mutations = self._class_outer_mutations
        inherited_mutations = self._mutations
        inherited_comprehension = self._comprehension_writes

        # Evaluation may happen after any surrounding declaration or rebinding.
        # Keep the alias's own name in that surrounding summary for recursion
        # and later writes, rather than treating its RHS as an eager assignment.
        self._aliases = self._index.summaries[node].copy()
        self._mutations = self._index.mutations[node].copy()
        self._class_outer_aliases = self._index.outer(node)
        self._class_outer_mutations = self._mutations.copy()
        self._comprehension_writes = []
        parent = self._index.parents[node]
        if isinstance(parent, ast.ClassDef):
            # An alias can be evaluated inside the class before a later class
            # attribute shadows an outer name. Deletion can expose that outer
            # name again, even when the attribute exists at alias creation.
            self._aliases = _merge_aliases(self._aliases, inherited_aliases)
            for name in self._index.bindings[parent].deleted_names:
                self._aliases[name] = self._aliases.get(name, _UNKNOWN) | (
                    self._class_outer_aliases.get(name, _UNKNOWN)
                )
        for parameter in node.type_params:
            for name in _bound_names(parameter):
                # All parameters, including later ones, shadow loaders in every
                # bound/default and in ordinary scopes nested inside the alias.
                self._aliases.pop(name, None)
                self._mutations.pop(name, None)
                self._class_outer_aliases.pop(name, None)
                self._class_outer_mutations.pop(name, None)
        try:
            # Direct annotation expressions keep their containing import scope
            # and TYPE_CHECKING context; nested lambdas/comprehensions establish
            # their normal local scopes. No source expressions are evaluated.
            for expression in _type_alias_expressions(node):
                self.visit(expression)
        finally:
            self._aliases = inherited_aliases
            self._class_outer_aliases = inherited_class_outer
            self._class_outer_mutations = inherited_class_mutations
            self._mutations = inherited_mutations
            self._comprehension_writes = inherited_comprehension

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_local_namespace(node)

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef
    visit_Lambda = visit_FunctionDef

    def _visit_local_namespace(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Lambda
    ) -> None:
        is_class = isinstance(node, ast.ClassDef)
        # Decorators and function defaults are eager. Generic class bases use
        # the containing namespace extended by the class's type parameters.
        for expression in _definition_expressions(node):
            if is_class and expression not in node.decorator_list:
                continue
            self.visit(expression)
        if is_class:
            outer_aliases = self._aliases
            outer_mutations = self._mutations
            self._aliases = outer_aliases.copy()
            self._mutations = outer_mutations.copy()
            for parameter in getattr(node, "type_params", ()):
                for name in _bound_names(parameter):
                    self._aliases.pop(name, None)
                    self._mutations.pop(name, None)
            for expression in (
                *node.bases,
                *(keyword.value for keyword in node.keywords),
            ):
                self.visit(expression)
            # Base-expression walrus writes belong to the containing namespace.
            for name, values in self._aliases.items():
                if not any(
                    name in _bound_names(p) for p in getattr(node, "type_params", ())
                ):
                    outer_aliases[name] = values
            self._aliases = outer_aliases
            self._mutations = outer_mutations
        inherited_aliases = self._aliases
        inherited_class_outer = self._class_outer_aliases
        inherited_class_mutations = self._class_outer_mutations
        inherited_mutations = self._mutations
        inherited_comprehension = self._comprehension_writes
        self._comprehension_writes = []
        body = [node.body] if isinstance(node, ast.Lambda) else node.body
        bindings = self._index.bindings[node]
        if is_class:
            outer = (
                inherited_class_outer
                if inherited_class_outer is not None
                else inherited_aliases
            )
            self._aliases = outer.copy()
            self._class_outer_aliases = outer
            self._mutations = (
                inherited_class_mutations
                if inherited_class_mutations is not None
                else inherited_mutations
            ).copy()
            self._class_outer_mutations = self._mutations.copy()
            # Class locals shadow inherited mutations sequentially below;
            # methods still use the surrounding non-class lexical summary.
            for name in bindings.names - bindings.globals - bindings.nonlocals:
                self._mutations.pop(name, None)
        else:
            outer = self._index.outer(node)
            for name in bindings.globals:
                outer[name] = self._global_aliases.get(name, _UNKNOWN)
            self._aliases = outer.copy()
            self._mutations = self._index.mutations[node].copy()
            self._class_outer_aliases = None
            self._class_outer_mutations = None
            # Method annotations see class attributes, while their bodies
            # skip that namespace. Arguments and body locals never shadow an
            # annotation's free names.
            parent = self._index.parents[node]
            if isinstance(parent, ast.ClassDef):
                self._aliases = self._index.summaries[parent].copy()
            self._visit_type_parameters(node)
            for argument in _arguments(node.args):
                if argument.annotation is not None:
                    self.visit(argument.annotation)
            if getattr(node, "returns", None) is not None:
                self.visit(node.returns)
            self._aliases = outer.copy()
            for name in bindings.names - bindings.globals - bindings.nonlocals:
                self._aliases.pop(name, None)
        if is_class:
            for name in bindings.globals:
                self._aliases[name] = self._global_aliases.get(name, _UNKNOWN)
            self._visit_type_parameters(node)
        for parameter in getattr(node, "type_params", ()):
            for name in _bound_names(parameter):
                self._aliases.pop(name, None)
                self._mutations.pop(name, None)
        self._local_depth += 1
        try:
            for statement in body:
                self.visit(statement)
        finally:
            self._local_depth -= 1
            self._aliases = inherited_aliases
            self._class_outer_aliases = inherited_class_outer
            self._class_outer_mutations = inherited_class_mutations
            self._mutations = inherited_mutations
            self._comprehension_writes = inherited_comprehension
            for name in _bound_names(node):
                self._aliases.pop(name, None)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        self.visit(node.value)
        alias = self._known_alias(node.value)
        for target in node.targets:
            self.visit(target)
            if isinstance(target, ast.Name):
                self._aliases[target.id] = alias

    def visit_AugAssign(self, node: ast.AugAssign) -> None:  # noqa: N802
        # Read the old target before writing it; a loader can occur on the RHS.
        if not isinstance(node.target, ast.Name):
            self.visit(node.target)
        self.visit(node.value)
        if isinstance(node.target, ast.Name):
            self._aliases.pop(node.target.id, None)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
            alias = self._known_alias(node.value)
            self.visit(node.target)
            if isinstance(node.target, ast.Name):
                self._aliases[node.target.id] = alias

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:  # noqa: N802
        self.visit(node.value)
        alias = self._known_alias(node.value)
        self.visit(node.target)
        self._aliases[node.target.id] = alias
        for writes in self._comprehension_writes:
            writes.add(node.target.id)

    def _visit_loop(self, node: ast.For | ast.AsyncFor | ast.While) -> None:
        if not isinstance(node, ast.While):
            self.visit(node.iter)
        incoming = self._aliases.copy()
        # Widen the loop head for subsequent iterations, retaining the initial
        # state and all writes. This also prevents a later iteration's guard
        # from being mistaken for an invariant TYPE_CHECKING alias.
        bindings = _LocalBindings()
        for statement in node.body:
            bindings.visit(statement)
        if not isinstance(node, ast.While):
            bindings.visit(node.target)
        self._aliases = _merge_aliases(incoming, bindings.summary(incoming))
        if isinstance(node, ast.While):
            self.visit(node.test)
        else:
            self.visit(node.target)
        exits = [incoming, self._aliases.copy()]
        for statement in node.body:
            self.visit(statement)
            exits.append(self._aliases.copy())
        loop_exit = _merge_aliases(*exits)
        normal_exit = self._path(node.orelse, loop_exit)
        self._aliases = _merge_aliases(loop_exit, normal_exit)

    visit_For = _visit_loop
    visit_AsyncFor = _visit_loop
    visit_While = _visit_loop

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:  # noqa: N802
        if node.type is not None:
            self.visit(node.type)
        for name in _bound_names(node):
            self._aliases.pop(name, None)
        for statement in node.body:
            self.visit(statement)
        for name in _bound_names(node):
            self._aliases.pop(name, None)

    def _visit_comprehension(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ) -> None:
        self.visit(node.generators[0].iter)
        inherited_aliases = self._aliases
        inherited_class_outer = self._class_outer_aliases
        inherited_class_mutations = self._class_outer_mutations
        inherited_mutations = self._mutations
        outer = (
            inherited_class_outer
            if inherited_class_outer is not None
            else inherited_aliases
        )
        self._aliases = outer.copy()
        if isinstance(node, ast.GeneratorExp):
            self._aliases = self._index.outer(node)
        bindings = self._index.bindings[node]
        self._aliases = _merge_aliases(self._aliases, bindings.summary(self._aliases))
        self._mutations = inherited_mutations.copy()
        self._class_outer_aliases = None
        self._class_outer_mutations = None
        for generator in node.generators:
            targets = _LocalBindings()
            targets.visit(generator.target)
            for name in targets.names:
                self._mutations.pop(name, None)
        writes: set[str] = set()
        self._comprehension_writes.append(writes)
        self._local_depth += 1
        try:
            for index, generator in enumerate(node.generators):
                if index:
                    self.visit(generator.iter)
                self.visit(generator.target)
                for condition in generator.ifs:
                    self.visit(condition)
            if isinstance(node, ast.DictComp):
                self.visit(node.key)
                self.visit(node.value)
            else:
                self.visit(node.elt)
            outgoing = self._aliases
        finally:
            self._local_depth -= 1
            self._aliases = inherited_aliases
            self._class_outer_aliases = inherited_class_outer
            self._class_outer_mutations = inherited_class_mutations
            self._mutations = inherited_mutations
            self._comprehension_writes.pop()
        # Zero iterations and unconsumed generators remain alternatives.
        for name in writes:
            self._aliases[name] = self._aliases.get(name, _UNKNOWN) | outgoing.get(
                name, _UNKNOWN
            )

    visit_ListComp = _visit_comprehension
    visit_SetComp = _visit_comprehension
    visit_DictComp = _visit_comprehension
    visit_GeneratorExp = _visit_comprehension

    @staticmethod
    def _call_argument(node: ast.Call, position: int, name: str) -> ast.expr | None:
        if position < len(node.args):
            return node.args[position]
        return next(
            (keyword.value for keyword in node.keywords if keyword.arg == name), None
        )

    def _literal_dynamic_target(self, node: ast.Call, callee: str) -> str | None:
        name = self._call_argument(node, 0, "name")
        if not isinstance(name, ast.Constant) or not isinstance(name.value, str):
            return None
        target = name.value
        if callee == "__import__":
            level = self._call_argument(node, 4, "level")
            if level is not None and (
                not isinstance(level, ast.Constant) or level.value != 0
            ):
                return None
        elif target.startswith("."):
            package = self._call_argument(node, 1, "package")
            if not isinstance(package, ast.Constant) or not isinstance(
                package.value, str
            ):
                return None
            level = len(target) - len(target.lstrip("."))
            parts = package.value.split(".")
            if level > len(parts):
                return None
            base = ".".join(parts[: len(parts) - level + 1])
            tail = target[level:]
            target = f"{base}.{tail}" if tail else base
        if not target or any(not part.isidentifier() for part in target.split(".")):
            return None
        return target

    def _known_alias(self, expression: ast.expr) -> frozenset[str]:
        base = expression.value if isinstance(expression, ast.Attribute) else expression
        if isinstance(base, ast.Name) and base.id in self._mutations:
            return _alias_values(
                expression,
                {
                    base.id: self._aliases.get(base.id, _UNKNOWN)
                    | self._mutations[base.id]
                },
            )
        return _alias_values(expression, self._aliases)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        callees = self._known_alias(node.func) & {"__import__", "import_module"}
        if callees:
            # Prefer import_module for the literal candidate if both loaders
            # are possible. Both already carry uncertain dynamic evidence.
            callee = "import_module" if "import_module" in callees else "__import__"
            target = self._literal_dynamic_target(node, callee)
            if target is not None:
                self.facts.append(
                    ImportFact(
                        id="",
                        source=self._module.id,
                        path=self._module.path,
                        line=node.lineno,
                        column=node.col_offset,
                        end_line=node.end_lineno,
                        end_column=node.end_col_offset,
                        alias_index=0,
                        syntax=ImportSyntax.DYNAMIC_IMPORT,
                        source_segment=ast.get_source_segment(self._source, node),
                        base_module=target,
                        imported_name=None,
                        as_name=None,
                        bound_name="",
                        relative_level=0,
                        scope=self._scope,
                        type_only=self._type_only,
                    )
                )
            self.diagnostics.append(
                Diagnostic(
                    severity=Severity.WARNING,
                    code="dynamic_import_ignored",
                    message=(
                        f"Dynamic import call {callee} has a static literal candidate; "
                        "runtime behaviour remains unknown."
                        if target is not None
                        else f"Dynamic import call {callee} was not resolved."
                    ),
                    path=self._module.path,
                    line=node.lineno,
                    column=node.col_offset,
                )
            )

        self.generic_visit(node)


class AstImportFactSource:
    """Collect syntactic import facts without importing or executing code."""

    def collect(
        self,
        source_root: Path,
        modules: tuple[SourceModule, ...],
    ) -> FactCollection:
        collection = self.collect_uncanonicalised(source_root, modules)
        return replace(collection, facts=canonicalise_fact_ids(collection.facts))

    def collect_uncanonicalised(
        self,
        source_root: Path,
        modules: tuple[SourceModule, ...],
    ) -> FactCollection:
        """Collect raw facts so orchestration can assign IDs exactly once."""

        facts: list[ImportFact] = []
        diagnostics: list[Diagnostic] = []

        for module in sorted(modules, key=lambda item: (item.id, item.path)):
            source_path = source_root / module.path
            try:
                # Follow regular-file symlinks, but never open a FIFO or device
                # supplied directly to the collector or changed since discovery.
                if not stat.S_ISREG(source_path.stat().st_mode):
                    diagnostics.append(
                        Diagnostic(
                            severity=Severity.ERROR,
                            code="source_not_regular",
                            message="Source input must be a regular file.",
                            path=module.path,
                        )
                    )
                    continue
                with tokenize.open(source_path) as source_file:
                    source = source_file.read()
            except OSError:
                diagnostics.append(
                    Diagnostic(
                        severity=Severity.ERROR,
                        code="source_read_error",
                        message="Python source could not be read.",
                        path=module.path,
                    )
                )
                continue
            except (LookupError, SyntaxError, UnicodeError) as error:
                diagnostics.append(
                    Diagnostic(
                        severity=Severity.ERROR,
                        code="source_decode_error",
                        message="Python source could not be decoded.",
                        path=module.path,
                        line=getattr(error, "lineno", None),
                        column=(
                            _syntax_error_column(error)
                            if isinstance(error, SyntaxError)
                            else None
                        ),
                    )
                )
                continue

            try:
                tree = ast.parse(source, filename=module.path)
                visitor = _ImportVisitor(module, source)
                visitor.visit(tree)
            except SyntaxError as error:
                diagnostics.append(
                    Diagnostic(
                        severity=Severity.ERROR,
                        code="source_syntax_error",
                        message="Python source could not be parsed.",
                        path=module.path,
                        line=error.lineno,
                        column=_syntax_error_column(error),
                    )
                )
                continue
            except RecursionError:
                diagnostics.append(
                    Diagnostic(
                        severity=Severity.ERROR,
                        code="source_analysis_limit",
                        message="Python source exceeded the parser or traversal recursion limit.",
                        path=module.path,
                    )
                )
                # None of this source's partial observations can establish a
                # successful extraction. Continue collecting other modules.
                continue

            facts.extend(visitor.facts)
            diagnostics.extend(visitor.diagnostics)

        return FactCollection(
            facts=tuple(facts),
            diagnostics=tuple(sorted(diagnostics, key=_diagnostic_sort_key)),
        )


__all__ = ["AstImportFactSource", "canonicalise_fact_ids"]
