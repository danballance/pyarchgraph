"""Legacy values used only by the retained collector benchmark.

The reference collector assigned IDs within extraction, including a temporary
empty-ID state. Keep that historical lifecycle local to benchmark tooling while
the runtime uses explicit drafts. The collector algorithm remains unchanged.
"""

from dataclasses import dataclass

from pyarchgraph.domain.models import Diagnostic, ImportContext, ImportSyntax


@dataclass(frozen=True, slots=True)
class ImportFact:
    id: str
    source: str
    path: str
    line: int
    column: int
    end_line: int | None
    end_column: int | None
    alias_index: int
    syntax: ImportSyntax
    source_segment: str | None
    base_module: str | None
    imported_name: str | None
    as_name: str | None
    bound_name: str
    relative_level: int
    context: ImportContext = ImportContext()


@dataclass(frozen=True, slots=True)
class FactCollection:
    facts: tuple[ImportFact, ...]
    diagnostics: tuple[Diagnostic, ...] = ()
