"""Citation forms the filing matrix actually cites.

The 申報規範 matrix rests on provisions like 所得稅法第66條之9 (未分配盈餘) and
產業創新條例第10條之1 (投資抵減), and on lists such as 所得稅法第88條、第92條
(扣繳義務). Both forms were unreachable: the sub-article was truncated to the
parent article, and only the first item of a list was seen — so a cell resting
on two provisions silently tracked only one.
"""

from __future__ import annotations

import pytest

from taxwatch.graph.citation import extract_citations
from taxwatch.normalize.tw_law_json import _build_node_key


def keys(text: str) -> list[str]:
    return [c.entity_key for c in extract_citations(text)]


class TestSubArticles:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("所得稅法第66條之9", "所得稅法#66-9"),
            ("所得稅法第66之9條", "所得稅法#66-9"),
            ("產業創新條例第10條之1", "產業創新條例#10-1"),
            ("所得稅法第十四條之一", "所得稅法#14-1"),
        ],
    )
    def test_sub_article_reaches_the_node_the_normalizer_mints(self, text, expected):
        assert expected in keys(text)

    def test_matches_the_normalizer_exactly(self):
        """A citation naming a node the normalizer never mints is unreachable."""
        assert _build_node_key("所得稅法", "第 66-9 條") == "所得稅法#66-9"
        assert "所得稅法#66-9" in keys("所得稅法第66條之9")

    def test_does_not_treat_prose_after_之_as_a_sub_article(self):
        """第14條之解釋 is 「the interpretation of Article 14」, not Article 14-X."""
        assert keys("所得稅法第14條之解釋") == ["所得稅法#14"]


class TestArticleRuns:
    @pytest.mark.parametrize("joiner", ["、", "及", "和", "與", ",", "，"])
    def test_every_article_in_a_list_is_captured(self, joiner):
        found = keys(f"所得稅法第88條{joiner}第92條")
        assert "所得稅法#88" in found
        assert "所得稅法#92" in found

    def test_three_item_run(self):
        found = keys("所得稅法第88條、第92條、第114條")
        assert {"所得稅法#88", "所得稅法#92", "所得稅法#114"} <= set(found)

    def test_run_items_inherit_the_law_of_the_first(self):
        found = keys("依產業創新條例第10條、第10條之1規定")
        assert "產業創新條例#10" in found
        assert "產業創新條例#10-1" in found

    def test_a_lone_article_is_unaffected(self):
        assert keys("所得稅法第71條") == ["所得稅法#71"]
