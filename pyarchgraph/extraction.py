"""Extract deterministic, source-backed import facts from Python ASTs."""

from __future__ import annotations

import ast
import hashlib
import json
import stat
import tokenize
from collections.abc import Iterable
from dataclasses import replace
from itertools import pairwise
from pathlib import Path

from pyarchgraph.model import (
    Diagnostic,
    FactCollection,
    ImportFact,
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
    for left, right in pairwise(ordered):
        common = 0
        for left_char, right_char in zip(left, right):
            if left_char != right_char:
                break
            common += 1
        length = max(_FACT_ID_PREFIX_LENGTH, common + 1)
        lengths[left] = max(lengths[left], length)
        lengths[right] = max(lengths[right], length)
    return tuple(lengths[digest] for digest in digests)


def canonicalise_fact_ids(facts: Iterable[ImportFact]) -> tuple[ImportFact, ...]:
    """Sort facts and assign stable, collision-safe content-derived IDs."""

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


def _character_column(lines: list[str], line: int, byte_column: int) -> int:
    """Convert an AST UTF-8 byte offset to a zero-based character column."""

    return len(lines[line - 1].encode("utf-8")[:byte_column].decode("utf-8"))


def _collect_import_facts(
    module: SourceModule, source: str, tree: ast.Module
) -> Iterable[ImportFact]:
    """Collect every explicit import, independent of its execution context."""

    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        is_from = isinstance(node, ast.ImportFrom)
        for alias_index, alias in enumerate(node.names):
            yield ImportFact(
                id="",
                source=module.id,
                path=module.path,
                line=node.lineno,
                column=node.col_offset,
                end_line=getattr(node, "end_lineno", None),
                end_column=getattr(node, "end_col_offset", None),
                alias_index=alias_index,
                syntax=ImportSyntax.IMPORT_FROM if is_from else ImportSyntax.IMPORT,
                source_segment=ast.get_source_segment(source, node),
                base_module=node.module if is_from else alias.name,
                imported_name=alias.name if is_from else None,
                as_name=alias.asname,
                bound_name=alias.asname
                or (alias.name if is_from else alias.name.partition(".")[0]),
                relative_level=node.level if is_from else 0,
            )


class AstImportFactSource:
    """Collect syntactic import facts without importing or executing code."""

    def collect(
        self,
        source_root: Path,
        modules: tuple[SourceModule, ...],
    ) -> FactCollection:
        """Collect all explicit imports and assign their canonical IDs once."""

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
                module_facts = tuple(_collect_import_facts(module, source, tree))
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

            # AST positions count UTF-8 bytes, unlike SyntaxError offsets and
            # the character columns exposed in findings.
            lines = source.split("\n")
            facts.extend(
                replace(
                    fact,
                    column=_character_column(lines, fact.line, fact.column),
                    end_column=(
                        _character_column(lines, fact.end_line, fact.end_column)
                        if fact.end_line is not None and fact.end_column is not None
                        else fact.end_column
                    ),
                )
                for fact in module_facts
            )

        return FactCollection(
            facts=canonicalise_fact_ids(facts),
            diagnostics=tuple(sorted(diagnostics, key=_diagnostic_sort_key)),
        )


__all__ = ["AstImportFactSource", "canonicalise_fact_ids"]
