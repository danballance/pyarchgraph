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
    """Choose deterministic digest prefix lengths, extending collisions."""

    lengths: list[int] = []
    for index, digest in enumerate(digests):
        length = _FACT_ID_PREFIX_LENGTH
        while any(
            other_index != index
            and other_digest != digest
            and other_digest.startswith(digest[:length])
            for other_index, other_digest in enumerate(digests)
        ):
            length += 1
        lengths.append(length)
    return tuple(lengths)


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


class _ImportVisitor(ast.NodeVisitor):
    """Collect imports while retaining only the context promised by v0.1."""

    def __init__(self, module: SourceModule, source: str) -> None:
        self._module = module
        self._source = source
        self._local_depth = 0
        self._type_only = False
        self._type_checking_names: set[str] = set()
        self._typing_aliases: set[str] = set()
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

            if self._scope is ImportScope.MODULE and alias.name == "typing":
                self._typing_aliases.add(bound_name)

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

            if (
                self._scope is ImportScope.MODULE
                and node.level == 0
                and node.module == "typing"
                and alias.name == "TYPE_CHECKING"
            ):
                self._type_checking_names.add(bound_name)

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
        if isinstance(test, ast.Name):
            return test.id in self._type_checking_names
        return (
            isinstance(test, ast.Attribute)
            and test.attr == "TYPE_CHECKING"
            and isinstance(test.value, ast.Name)
            and test.value.id in self._typing_aliases
        )

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
        self._local_depth += 1
        try:
            self.generic_visit(node)
        finally:
            self._local_depth -= 1

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        callee: str | None = None
        if isinstance(node.func, ast.Name) and node.func.id == "__import__":
            callee = "__import__"
        elif (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "import_module"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "importlib"
        ):
            callee = "importlib.import_module"

        if callee is not None:
            self.diagnostics.append(
                Diagnostic(
                    severity=Severity.WARNING,
                    code="dynamic_import_ignored",
                    message=f"Dynamic import call {callee} was not resolved.",
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
            facts=_assign_fact_ids(facts),
            diagnostics=tuple(sorted(diagnostics, key=_diagnostic_sort_key)),
        )


__all__ = ["AstImportFactSource", "canonicalise_fact_ids"]
