"""A citation must not name two laws at once, nor half of one.

營利事業所得稅查核準則第1條 names its authority as 所得稅法第八十條第五項. When a
provision names two, the law-name pattern used to run straight across the
article number into the second law and mint a single statute called
「所得稅法第八十條第五項及稅捐稽徵法」. The 查核準則 was filed under that, its
consolidated view rooted on 稅捐稽徵法 (74 articles of collection procedure),
and an entire extraction produced 破產財團 and 滯納金 rows for 營利事業所得稅.
"""

from __future__ import annotations

import pytest

from taxwatch.graph.citation import extract_citations


def keys(text: str) -> list[str]:
    return [c.entity_key for c in extract_citations(text)]


class TestTwoLawsInOneProvision:
    TEXT = "本準則依所得稅法第八十條第五項及稅捐稽徵法第一條之一規定訂定之。"

    def test_the_authority_is_the_first_law_not_a_merger(self):
        assert "所得稅法#80" in keys(self.TEXT)

    def test_the_second_law_is_its_own_entity(self):
        assert "稅捐稽徵法#1-1" in keys(self.TEXT)

    def test_no_merged_statute_is_minted(self):
        for key in keys(self.TEXT):
            name = key.split("#", 1)[0]
            assert "第" not in name, f"law name spans an article number: {name}"
            assert not (name.startswith("五") or name.startswith("項")), name


class TestRealNamesSurvive:
    """Conjunctions cannot simply be excluded — real statutes contain them."""

    @pytest.mark.parametrize(
        "text,expected",
        [
            # 及 and 與 both inside one genuine name
            ("依遺產及贈與稅法第17條規定", "遺產及贈與稅法#17"),
            # 和 inside 中华人民共和国
            ("根据中华人民共和国企业所得税法第五条", "中华人民共和国企业所得税法#5"),
            ("根据《中华人民共和国增值税法》第十二条", "中华人民共和国增值税法#12"),
        ],
    )
    def test_name_is_kept_whole(self, text, expected):
        assert expected in keys(text)


class TestLeadingVerbs:
    def test_依據_is_not_left_as_據(self):
        """「依據所得稅法」 matched from the second character left 據所得稅法."""
        found = keys("依據所得稅法第14條第1項第5類規定")
        assert "所得稅法#14" in found
        assert not any(k.startswith("據") for k in found), found


def test_single_authority_is_unchanged():
    assert "所得稅法#80" in keys("本準則依所得稅法第八十條第五項規定訂定之。")
