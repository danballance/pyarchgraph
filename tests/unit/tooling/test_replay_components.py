"""Independent graph audit logic uses plain Python values."""

import pytest

from benchmarks.replay_validation import components


@pytest.mark.parametrize(
    "pairs,expected",
    [
        ([], []),
        ([("a", "b")], []),
        ([("a", "a"), ("a", "b")], [["a"]]),
        ([("a", "b"), ("b", "a"), ("b", "c")], [["a", "b"]]),
        ([("a", "b"), ("b", "c"), ("c", "b")], [["b", "c"]]),
        (
            [("a", "b"), ("b", "a"), ("b", "c"), ("c", "d"), ("d", "c")],
            [["a", "b"], ["c", "d"]],
        ),
    ],
)
def test_independent_components_respect_disconnected_and_one_way_relations(
    pairs, expected
):
    assert components(pairs) == expected
    assert components(list(reversed(pairs))) == expected
