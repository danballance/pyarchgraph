"""Draft facts acquire deterministic domain identities without I/O."""

import hashlib
import json
import random
from dataclasses import replace

import pytest

from pyarchgraph.domain.canonicalization import FactCanonicalizer
from pyarchgraph.domain.models import ImportFact, ImportFactDraft, ImportSyntax


def _draft(target: str, *, source: str = "mod") -> ImportFactDraft:
    return ImportFactDraft(
        source=source,
        path=f"{source}.py",
        line=1,
        column=0,
        end_line=1,
        end_column=len(f"import {target}"),
        alias_index=0,
        syntax=ImportSyntax.IMPORT,
        source_segment=f"import {target}",
        base_module=target,
        imported_name=None,
        as_name=None,
        bound_name=target,
        relative_level=0,
    )


def test_fact_ids_and_order_are_deterministic() -> None:
    a = _draft("y", source="a")
    b = _draft("z", source="b")
    canonicalizer = FactCanonicalizer()
    forward = canonicalizer.canonicalise((a, b))
    reverse = canonicalizer.canonicalise((b, a))

    assert forward == reverse
    assert [fact.source for fact in forward] == ["a", "b"]
    assert all(
        fact.id.startswith("fact-") and len(fact.id) == len("fact-") + 12
        for fact in forward
    )

    first = forward[0]
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
        first.source_segment,
        (
            first.context.scope,
            first.context.in_function,
            first.context.typing_only,
            first.context.conditional,
            first.context.exception_handler,
            first.context.package_initializer,
        ),
    )
    encoded = json.dumps(
        identity,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    assert first.id == f"fact-{hashlib.sha256(encoded).hexdigest()[:12]}"


def test_fact_id_prefix_collisions_are_extended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digests = {
        "a": "123456789abc0" + "0" * 51,
        "b": "123456789abc1" + "1" * 51,
        "c": "fedcba9876542" + "2" * 51,
    }
    monkeypatch.setattr(
        FactCanonicalizer,
        "_fact_digest",
        lambda self, fact: digests[fact.base_module or ""],
    )

    result = FactCanonicalizer().canonicalise(
        tuple(_draft(name) for name in ("a", "b", "c"))
    )

    assert [fact.id for fact in result] == [
        "fact-123456789abc0",
        "fact-123456789abc1",
        "fact-fedcba987654",
    ]


def test_full_fact_digest_collision_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(FactCanonicalizer, "_fact_digest", lambda self, fact: "0" * 64)

    with pytest.raises(ValueError, match="unique full SHA-256"):
        FactCanonicalizer().canonicalise((_draft("a"), _draft("b")))


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

    assert FactCanonicalizer()._minimum_unique_prefix_lengths(tuple(digests)) == tuple(
        expected
    )
    assert FactCanonicalizer()._minimum_unique_prefix_lengths(()) == ()
    assert FactCanonicalizer()._minimum_unique_prefix_lengths(("f" * 64,)) == (12,)


def test_draft_has_no_identity_until_canonicalized() -> None:
    draft = _draft("target")
    assert not hasattr(draft, "id")
    (fact,) = FactCanonicalizer().canonicalise((draft,))
    assert isinstance(fact, ImportFact)
    assert fact.id.startswith("fact-")
    assert not hasattr(draft, "id")


@pytest.mark.parametrize("invalid_id", ["", None, 1])
def test_canonical_fact_requires_nonempty_string_identity(invalid_id) -> None:
    (fact,) = FactCanonicalizer().canonicalise((_draft("target"),))
    with pytest.raises(ValueError):
        replace(fact, id=invalid_id)
