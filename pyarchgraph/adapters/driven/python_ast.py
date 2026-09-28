"""Filesystem decoding and Python AST adapters for immutable import facts."""

from __future__ import annotations

import ast
import re
import stat
import tokenize
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Protocol

from pyarchgraph.application.ports.sources import FactCollection, ImportFactSource
from pyarchgraph.domain.models import (
    Diagnostic,
    ImportContext,
    ImportFactDraft,
    ImportSyntax,
    Severity,
    SourceModule,
)

_PHYSICAL_LINE = re.compile(r"(.*?(?:\r\n|\n|\r|$))")


class SourceTextReader(Protocol):
    """Provide Python source text for import collection."""

    def read(self, path: Path) -> str: ...


class AstParser(Protocol):
    """Provide the syntax tree used to collect imports from Python source."""

    def parse(self, source: str, filename: str) -> ast.Module: ...


class ImportExtractor(Protocol):
    """Describe explicit imports and their context as import fact drafts."""

    def extract(
        self, module: SourceModule, source: str, tree: ast.Module
    ) -> Iterable[ImportFactDraft]: ...


class NonRegularSourceError(OSError):
    """Signal that an input cannot be read as a regular source file."""


class SourceReader(SourceTextReader):
    """Read Python source text, following only symlinks to regular files."""

    def read(self, path: Path) -> str:
        # Never open a FIFO or device, including inputs changed after discovery.
        if not stat.S_ISREG(path.stat().st_mode):
            raise NonRegularSourceError(str(path))
        with tokenize.open(path) as source_file:
            return source_file.read()


class PythonAstParser(AstParser):
    """Expose Python syntax for import collection without executing the source."""

    def parse(self, source: str, filename: str) -> ast.Module:
        return ast.parse(source, filename=filename)


class _SourceText:
    """Recover source excerpts and character columns for import fact drafts.

    Physical UTF-8 lines stay intact, without splitting Unicode separators.
    """

    def __init__(self, source: str) -> None:
        self.lines = tuple(
            (line.encode("utf-8") for line in _PHYSICAL_LINE.findall(source))
        )

    def column(self, line: int, byte_column: int) -> int:
        return len(self.lines[line - 1][:byte_column].decode("utf-8"))

    def segment(self, node: ast.AST) -> str | None:
        line = getattr(node, "lineno", None)
        end_line = getattr(node, "end_lineno", None)
        column = getattr(node, "col_offset", None)
        end_column = getattr(node, "end_col_offset", None)
        if any((value is None for value in (line, end_line, column, end_column))):
            return None
        if line == end_line:
            return self.lines[line - 1][column:end_column].decode("utf-8")
        return b"".join(
            (
                self.lines[line - 1][column:],
                *self.lines[line : end_line - 1],
                self.lines[end_line - 1][:end_column],
            )
        ).decode("utf-8")


class ModuleImportExtractor(ImportExtractor):
    """Describe each explicit import and its context as an import fact draft."""

    def _typing_aliases(self, tree: ast.Module) -> tuple[set[str], set[str]]:
        """Recognize unambiguous module bindings, conservatively across the file.

        Scoped, conditional, negated and compound bindings are not inferred. Any
        observed shadowing disables a name throughout the file, including uses
        before the shadowing site. No target code or annotation is evaluated.
        """
        direct: set[str] = set()
        qualified: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                qualified.update(
                    (
                        alias.asname or "typing"
                        for alias in node.names
                        if alias.name == "typing"
                    )
                )
            elif (
                isinstance(node, ast.ImportFrom)
                and node.level == 0
                and (node.module == "typing")
            ):
                direct.update(
                    (
                        alias.asname or "TYPE_CHECKING"
                        for alias in node.names
                        if alias.name == "TYPE_CHECKING"
                    )
                )
        if not direct and (not qualified):
            return (set(), set())
        conflicting: set[str] = set()
        module_statements = set(tree.body)
        wildcard = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname or alias.name.partition(".")[0]
                    if node in module_statements and alias.name == "typing":
                        qualified.add(name)
                    else:
                        conflicting.add(name)
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    name = alias.asname or alias.name
                    wildcard |= alias.name == "*"
                    if (
                        node in module_statements
                        and node.level == 0
                        and (node.module == "typing")
                        and (alias.name == "TYPE_CHECKING")
                    ):
                        direct.add(name)
                    else:
                        conflicting.add(name)
            elif isinstance(node, ast.Name) and isinstance(
                node.ctx, (ast.Store, ast.Del)
            ):
                conflicting.add(node.id)
            elif isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                conflicting.add(node.name)
            elif isinstance(node, ast.arg):
                conflicting.add(node.arg)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                conflicting.add(node.name)
            elif isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name:
                conflicting.add(node.name)
            elif isinstance(node, ast.MatchMapping) and node.rest:
                conflicting.add(node.rest)
            elif (
                isinstance(node, ast.Attribute)
                and isinstance(node.ctx, (ast.Store, ast.Del))
                and (node.attr == "TYPE_CHECKING")
                and isinstance(node.value, ast.Name)
            ):
                conflicting.add(node.value.id)
            elif type(node).__name__ in {"TypeVar", "TypeVarTuple", "ParamSpec"}:
                conflicting.add(node.name)
        if wildcard:
            return (set(), set())
        # A spelling shared by direct and qualified forms is ambiguous too.
        conflicting.update(direct & qualified)
        return (direct - conflicting, qualified - conflicting)

    def _imports_with_context(
        self, module: SourceModule, tree: ast.Module
    ) -> Iterable[tuple[ast.Import | ast.ImportFrom, ImportContext]]:
        direct, qualified = self._typing_aliases(tree)
        initial = ImportContext(package_initializer=module.is_package)
        pending: list[tuple[ast.AST, ImportContext]] = [(tree, initial)]
        guarded = (
            ast.If,
            ast.For,
            ast.AsyncFor,
            ast.While,
            ast.Try,
            ast.TryStar,
            ast.With,
            ast.AsyncWith,
            ast.Match,
        )
        while pending:
            node, context = pending.pop()
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                yield (node, context)
                continue
            type_checking = isinstance(node, ast.If) and (
                isinstance(node.test, ast.Name)
                and node.test.id in direct
                or (
                    isinstance(node.test, ast.Attribute)
                    and node.test.attr == "TYPE_CHECKING"
                    and isinstance(node.test.value, ast.Name)
                    and (node.test.value.id in qualified)
                )
            )
            for field, value in ast.iter_fields(node):
                child_context = context
                if field == "body" and isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    child_context = replace(
                        child_context, scope="function", in_function=True
                    )
                elif field == "body" and isinstance(node, ast.ClassDef):
                    child_context = replace(child_context, scope="class")
                if isinstance(node, guarded):
                    child_context = replace(child_context, conditional=True)
                if type_checking and field == "body":
                    child_context = replace(child_context, typing_only=True)
                if isinstance(node, ast.ExceptHandler):
                    child_context = replace(child_context, exception_handler=True)
                for child in value if isinstance(value, list) else (value,):
                    # Explicit imports cannot occur in expressions. Alias analysis
                    # above still traverses them to detect binding and shadowing.
                    if isinstance(child, ast.AST) and (not isinstance(child, ast.expr)):
                        pending.append((child, child_context))

    def extract(
        self, module: SourceModule, source: str, tree: ast.Module
    ) -> Iterable[ImportFactDraft]:
        """Collect every explicit import and annotate its syntactic context."""
        text = _SourceText(source)
        for node, context in self._imports_with_context(module, tree):
            is_from = isinstance(node, ast.ImportFrom)
            segment = text.segment(node)
            column = text.column(node.lineno, node.col_offset)
            end_line = getattr(node, "end_lineno", None)
            end_column = getattr(node, "end_col_offset", None)
            if end_line is not None and end_column is not None:
                end_column = text.column(end_line, end_column)
            for alias_index, alias in enumerate(node.names):
                yield ImportFactDraft(
                    source=module.id,
                    path=module.path,
                    line=node.lineno,
                    column=column,
                    end_line=end_line,
                    end_column=end_column,
                    alias_index=alias_index,
                    syntax=ImportSyntax.IMPORT_FROM if is_from else ImportSyntax.IMPORT,
                    source_segment=segment,
                    base_module=node.module if is_from else alias.name,
                    imported_name=alias.name if is_from else None,
                    as_name=alias.asname,
                    bound_name=alias.asname
                    or (alias.name if is_from else alias.name.partition(".")[0]),
                    relative_level=node.level if is_from else 0,
                    context=context,
                )


class AstImportFactSource(ImportFactSource):
    """Collect import fact drafts and source diagnostics without executing code."""

    def __init__(
        self,
        reader: SourceTextReader,
        parser: AstParser,
        extractor: ImportExtractor,
    ) -> None:
        self.reader = reader
        self.parser = parser
        self.extractor = extractor

    def collect(
        self, source_root: Path, modules: tuple[SourceModule, ...]
    ) -> FactCollection:
        """Collect import drafts; application assembly assigns final identities."""
        facts: list[ImportFactDraft] = []
        diagnostics: list[Diagnostic] = []
        for module in sorted(modules, key=lambda item: (item.id, item.path)):
            source_path = source_root / module.path
            try:
                source = self.reader.read(source_path)
            except NonRegularSourceError:
                diagnostics.append(
                    Diagnostic(
                        severity=Severity.ERROR,
                        code="source_not_regular",
                        message="Source input must be a regular file.",
                        path=module.path,
                    )
                )
                continue
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
                        column=self._syntax_error_column(error)
                        if isinstance(error, SyntaxError)
                        else None,
                    )
                )
                continue
            try:
                tree = self.parser.parse(source, filename=module.path)
                module_facts = tuple(self.extractor.extract(module, source, tree))
            except SyntaxError as error:
                diagnostics.append(
                    Diagnostic(
                        severity=Severity.ERROR,
                        code="source_syntax_error",
                        message="Python source could not be parsed.",
                        path=module.path,
                        line=error.lineno,
                        column=self._syntax_error_column(error),
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
                continue
            facts.extend(module_facts)
        return FactCollection(
            facts=tuple(facts),
            diagnostics=tuple(sorted(diagnostics, key=self._diagnostic_sort_key)),
        )

    def _diagnostic_sort_key(self, diagnostic: Diagnostic) -> tuple[object, ...]:
        return (
            diagnostic.severity.value,
            diagnostic.code,
            diagnostic.path or "",
            self._nullable_integer(diagnostic.line),
            self._nullable_integer(diagnostic.column),
            diagnostic.message,
        )

    def _syntax_error_column(self, error: SyntaxError) -> int | None:
        if error.offset is None:
            return None
        return max(error.offset - 1, 0)

    def _nullable_integer(self, value: int | None) -> int:
        return -1 if value is None else value
