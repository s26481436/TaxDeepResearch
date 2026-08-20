"""Which of several declared authorities is the real 母法.

營利事業所得稅查核準則第1條 cites two laws: 所得稅法第八十條第五項 and
稅捐稽徵法第一條之一. Taking whichever edge happened to be written first rooted
the family on 稅捐稽徵法, and an extraction for 營利事業所得稅 came back with
核課期間, 滯納金 and 破產財團 rows — 74 articles of collection procedure
standing in for the statute the 準則 actually implements.
"""

from __future__ import annotations

import pytest

from taxwatch.services.consolidated import _closest_parent


class _Entity:
    def __init__(self, key: str):
        self.entity_key = key

    def __repr__(self) -> str:  # pragma: no cover - test output only
        return self.entity_key


def keys(entities):
    return [e.entity_key for e in entities]


class TestTwoDeclaredAuthorities:
    PARENTS = [_Entity("稅捐稽徵法"), _Entity("所得稅法")]

    def test_picks_the_statute_the_child_is_named_after(self):
        chosen = _closest_parent(self.PARENTS, "營利事業所得稅查核準則")
        assert chosen.entity_key == "所得稅法"

    def test_result_does_not_depend_on_edge_order(self):
        """The bug was order dependence — the older, wrong edge won."""
        chosen = _closest_parent(list(reversed(self.PARENTS)), "營利事業所得稅查核準則")
        assert chosen.entity_key == "所得稅法"


def test_single_parent_is_returned_unchanged():
    parents = [_Entity("增值税法")]
    assert _closest_parent(parents, "增值税法实施条例").entity_key == "增值税法"


def test_falls_back_to_the_more_specific_name_when_none_match():
    """No shared stem — prefer the longer, more specific statute over the general act."""
    parents = [_Entity("稅捐稽徵法"), _Entity("加值型及非加值型營業稅法")]
    chosen = _closest_parent(parents, "統一發票使用辦法")
    assert chosen.entity_key == "加值型及非加值型營業稅法"


@pytest.mark.parametrize(
    "child_title,expected",
    [
        ("增值税法实施条例", "增值税法"),
        ("營利事業所得稅查核準則", "所得稅法"),
        ("遺產及贈與稅法施行細則", "遺產及贈與稅法"),
    ],
)
def test_common_child_shapes(child_title, expected):
    parents = [_Entity("稅捐稽徵法"), _Entity(expected)]
    assert _closest_parent(parents, child_title).entity_key == expected
