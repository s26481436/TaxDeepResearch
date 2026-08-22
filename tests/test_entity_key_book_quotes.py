"""《》 quote a law's name; they are never part of it.

`strip("《》")` only removed them from the ends, so any key where the title was
not the whole string kept a stray closing bracket — and a key carrying 》
matches no node, no entity, and no relation.
"""

from __future__ import annotations

import pytest

from taxwatch.graph.resolver import normalize_entity_key


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("《所得稅法》", "所得稅法"),
        ("所得稅法", "所得稅法"),
        # An article-level key: the title is followed by #4, so the closing
        # bracket used to survive as 所得稅法》#4.
        ("《所得稅法》#4", "所得稅法#4"),
        # A child regulation: the parent's name is quoted mid-title.
        ("《中华人民共和国增值税法》实施条例", "增值税法实施条例"),
        ("依《所得稅法》第4條", "依所得稅法第4條"),
        ("《遺產及贈與稅法》#17-1", "遺產及贈與稅法#17-1"),
    ],
)
def test_book_quotes_are_removed_wherever_they_appear(raw, expected):
    assert normalize_entity_key(raw) == expected


def test_a_quoted_article_key_matches_its_unquoted_form():
    """The whole point: the two spellings must resolve to one key."""
    assert normalize_entity_key("《所得稅法》#4") == normalize_entity_key("所得稅法#4")


def test_the_prc_prefix_still_goes_after_the_quotes():
    """Order matters — the prefix strip anchors at the start of the string."""
    assert normalize_entity_key("《中华人民共和国增值税法》") == "增值税法"
