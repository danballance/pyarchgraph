"""Deterministic, filesystem-independent import-fact identities."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from itertools import pairwise

from pyarchgraph.domain.models import ImportContext, ImportFact, ImportFactDraft

_FACT_ID_PREFIX_LENGTH = 12


class FactCanonicalizer:
    """Assign stable, collision-safe identities to immutable import facts."""

    def _context_identity(self, context: ImportContext) -> tuple[object, ...]:
        return (
            context.scope,
            context.in_function,
            context.typing_only,
            context.conditional,
            context.exception_handler,
            context.package_initializer,
        )

    def _nullable_string(self, value: str | None) -> str:
        return "" if value is None else value

    def _nullable_integer(self, value: int | None) -> int:
        return -1 if value is None else value

    def _fact_sort_key(self, fact: ImportFactDraft) -> tuple[object, ...]:
        """Return the complete, normalized source-site ordering key."""
        return (
            fact.source,
            fact.path,
            fact.line,
            fact.column,
            self._nullable_integer(fact.end_line),
            self._nullable_integer(fact.end_column),
            fact.alias_index,
            fact.syntax.value,
            self._nullable_string(fact.base_module),
            self._nullable_string(fact.imported_name),
            self._nullable_string(fact.as_name),
            fact.bound_name,
            fact.relative_level,
            self._nullable_string(fact.source_segment),
            self._context_identity(fact.context),
        )

    def _fact_identity(self, fact: ImportFactDraft) -> tuple[object, ...]:
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
            self._context_identity(fact.context),
        )

    def _fact_digest(self, fact: ImportFactDraft) -> str:
        encoded_identity = json.dumps(
            self._fact_identity(fact),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded_identity).hexdigest()

    def _minimum_unique_prefix_lengths(
        self, digests: tuple[str, ...]
    ) -> tuple[int, ...]:
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
        return tuple((lengths[digest] for digest in digests))

    def canonicalise(self, facts: Iterable[ImportFactDraft]) -> tuple[ImportFact, ...]:
        """Sort facts and assign stable, collision-safe content-derived IDs."""
        ordered = tuple(sorted(facts, key=self._fact_sort_key))
        digests = tuple((self._fact_digest(fact) for fact in ordered))
        if len(set(digests)) != len(digests):
            raise ValueError(
                "import facts must be unique and produce unique full SHA-256 digests"
            )
        lengths = self._minimum_unique_prefix_lengths(digests)
        return tuple(
            (
                ImportFact(
                    id=f"fact-{digest[:length]}",
                    source=fact.source,
                    path=fact.path,
                    line=fact.line,
                    column=fact.column,
                    end_line=fact.end_line,
                    end_column=fact.end_column,
                    alias_index=fact.alias_index,
                    syntax=fact.syntax,
                    source_segment=fact.source_segment,
                    base_module=fact.base_module,
                    imported_name=fact.imported_name,
                    as_name=fact.as_name,
                    bound_name=fact.bound_name,
                    relative_level=fact.relative_level,
                    context=fact.context,
                )
                for fact, digest, length in zip(ordered, digests, lengths, strict=True)
            )
        )
