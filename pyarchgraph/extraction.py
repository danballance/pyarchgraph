"""Extract deterministic, source-backed import facts from Python ASTs."""

from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tokenize
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


class _LocalBindings(ast.NodeVisitor):
    """Find names that shadow outer aliases throughout a function body."""

    def __init__(self) -> None:
        self.names: set[str] = set()
        self.outer_names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        self.names.update(
            alias.asname or alias.name.partition(".")[0] for alias in node.names
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        self.names.update(alias.asname or alias.name for alias in node.names)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self.names.add(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        pass

    def visit_Global(self, node: ast.Global) -> None:  # noqa: N802
        self.outer_names.update(node.names)

    visit_Nonlocal = visit_Global

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:  # noqa: N802
        if node.name is not None:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:  # noqa: N802
        # Comprehension targets belong to their own implicit function scope.
        for generator in node.generators:
            self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)
        self.visit(node.elt)

    visit_SetComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_DictComp(self, node: ast.DictComp) -> None:  # noqa: N802
        for generator in node.generators:
            self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)
        self.visit(node.key)
        self.visit(node.value)


class _ImportVisitor(ast.NodeVisitor):
    """Collect imports while retaining only the context promised by v0.1."""

    def __init__(self, module: SourceModule, source: str) -> None:
        self._module = module
        self._source = source
        self._local_depth = 0
        self._type_only = False
        self._aliases: dict[str, str] = {"__import__": "__import__"}
        self._class_outer_aliases: dict[str, str] | None = None
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

            self._aliases.pop(bound_name, None)
            if alias.name == "importlib" or (
                alias.name.startswith("importlib.") and alias.asname is None
            ):
                self._aliases[bound_name] = "importlib"
            elif alias.name == "typing":
                self._aliases[bound_name] = "typing"

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

            self._aliases.pop(bound_name, None)
            if (
                node.level == 0
                and node.module == "importlib"
                and alias.name == "import_module"
            ):
                self._aliases[bound_name] = "import_module"
            elif (
                node.level == 0
                and node.module == "typing"
                and alias.name == "TYPE_CHECKING"
            ):
                self._aliases[bound_name] = "type_checking"

    def visit_If(self, node: ast.If) -> None:  # noqa: N802
        # The test remains outside the guarded body and can itself contain a
        # dynamic import call that should be diagnosed.
        self.visit(node.test)
        recognised_guard = self._is_type_checking_guard(node.test)
        inherited_type_only = self._type_only

        if recognised_guard:
            self._type_only = True
        for statement in node.body:
            self.visit(statement)

        self._type_only = inherited_type_only
        for statement in node.orelse:
            self.visit(statement)

        self._type_only = inherited_type_only

    def _is_type_checking_guard(self, test: ast.expr) -> bool:
        return self._known_alias(test) == "type_checking"

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_local_namespace(node)

    def visit_AsyncFunctionDef(  # noqa: N802
        self, node: ast.AsyncFunctionDef
    ) -> None:
        self._visit_local_namespace(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self._visit_local_namespace(node)

    def _visit_local_namespace(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    ) -> None:
        # Decorators, defaults and class bases are evaluated in the surrounding
        # namespace; only the body introduces local bindings.
        for decorator in node.decorator_list:
            self.visit(decorator)
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                self.visit(base)
            for keyword in node.keywords:
                self.visit(keyword.value)
        else:
            self.visit(node.args)
            if node.returns is not None:
                self.visit(node.returns)
        if not isinstance(node, ast.ClassDef):
            self._aliases.pop(node.name, None)
        inherited_aliases = self._aliases
        inherited_class_outer = self._class_outer_aliases
        if isinstance(node, ast.ClassDef):
            self._aliases = inherited_aliases.copy()
            self._class_outer_aliases = (
                inherited_class_outer
                if inherited_class_outer is not None
                else inherited_aliases
            )
        else:
            # A method's free names skip the class attribute namespace.
            self._aliases = (
                inherited_class_outer
                if inherited_class_outer is not None
                else inherited_aliases
            ).copy()
            self._class_outer_aliases = None
        if not isinstance(node, ast.ClassDef):
            bindings = _LocalBindings()
            for statement in node.body:
                bindings.visit(statement)
            for name in bindings.names - bindings.outer_names:
                self._aliases.pop(name, None)
            for argument in (
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ):
                self._aliases.pop(argument.arg, None)
            for argument in (node.args.vararg, node.args.kwarg):
                if argument is not None:
                    self._aliases.pop(argument.arg, None)
        self._local_depth += 1
        try:
            for statement in node.body:
                self.visit(statement)
        finally:
            self._local_depth -= 1
            self._aliases = inherited_aliases
            self._class_outer_aliases = inherited_class_outer
            # Class names are bound only after the class body has executed.
            if isinstance(node, ast.ClassDef):
                self._aliases.pop(node.name, None)

    def visit_Name(self, node: ast.Name) -> None:  # noqa: N802
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self._aliases.pop(node.id, None)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        self.visit(node.value)
        alias = self._known_alias(node.value)
        for target in node.targets:
            self.visit(target)
            if alias is not None and isinstance(target, ast.Name):
                self._aliases[target.id] = alias

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
            alias = self._known_alias(node.value)
            self.visit(node.target)
            if alias is not None and isinstance(node.target, ast.Name):
                self._aliases[node.target.id] = alias

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:  # noqa: N802
        self.visit(node.value)
        self.visit(node.target)

    def visit_For(self, node: ast.For) -> None:  # noqa: N802
        # The iterator expression uses the old binding of the loop target.
        self.visit(node.iter)
        self.visit(node.target)
        for statement in (*node.body, *node.orelse):
            self.visit(statement)

    visit_AsyncFor = visit_For

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:  # noqa: N802
        if node.type is not None:
            self.visit(node.type)
        if node.name is not None:
            self._aliases.pop(node.name, None)
        for statement in node.body:
            self.visit(statement)

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        self.visit(node.args)
        inherited_aliases = self._aliases
        inherited_class_outer = self._class_outer_aliases
        self._aliases = (
            inherited_class_outer
            if inherited_class_outer is not None
            else inherited_aliases
        ).copy()
        self._class_outer_aliases = None
        bindings = _LocalBindings()
        bindings.visit(node.body)
        for name in bindings.names:
            self._aliases.pop(name, None)
        for argument in (
            *node.args.posonlyargs,
            *node.args.args,
            *node.args.kwonlyargs,
        ):
            self._aliases.pop(argument.arg, None)
        for argument in (node.args.vararg, node.args.kwarg):
            if argument is not None:
                self._aliases.pop(argument.arg, None)
        self._local_depth += 1
        try:
            self.visit(node.body)
        finally:
            self._local_depth -= 1
            self._aliases = inherited_aliases
            self._class_outer_aliases = inherited_class_outer

    def _visit_comprehension(
        self, node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
    ) -> None:
        self.visit(node.generators[0].iter)
        inherited_aliases = self._aliases
        inherited_class_outer = self._class_outer_aliases
        self._aliases = (
            inherited_class_outer
            if inherited_class_outer is not None
            else inherited_aliases
        ).copy()
        self._class_outer_aliases = None
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
        finally:
            self._local_depth -= 1
            self._aliases = inherited_aliases
            self._class_outer_aliases = inherited_class_outer

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

    def _known_alias(self, expression: ast.expr) -> str | None:
        if isinstance(expression, ast.Name):
            return self._aliases.get(expression.id)
        if isinstance(expression, ast.Attribute) and isinstance(
            expression.value, ast.Name
        ):
            module = self._aliases.get(expression.value.id)
            if module == "importlib" and expression.attr == "import_module":
                return "import_module"
            if module == "typing" and expression.attr == "TYPE_CHECKING":
                return "type_checking"
        return None

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        callee = self._known_alias(node.func)

        if callee in {"__import__", "import_module"}:
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

            visitor = _ImportVisitor(module, source)
            visitor.visit(tree)
            facts.extend(visitor.facts)
            diagnostics.extend(visitor.diagnostics)

        return FactCollection(
            facts=tuple(facts),
            diagnostics=tuple(sorted(diagnostics, key=_diagnostic_sort_key)),
        )


__all__ = ["AstImportFactSource", "canonicalise_fact_ids"]
