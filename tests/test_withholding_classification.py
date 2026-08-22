"""扣繳 is a way of collecting 所得稅, not a tax of its own.

The requirement dimensions already make this call — 扣繳 is a `tax_scheme`,
never a taxpayer class or a tax type. The taxonomy disagreed with itself:
各類所得扣繳率標準 sets the rates for 所得稅 withholding, but its title says
所得, never 所得稅, so it matched nothing and landed in tw_other — out of reach
of the 所得稅 matrix it governs.
"""

from __future__ import annotations

import pytest

from taxwatch.taxonomy import classify


@pytest.mark.parametrize(
    "title",
    ["各類所得扣繳率標準", "薪資所得扣繳辦法", "扣繳義務人違章案件裁罰基準"],
)
def test_withholding_rules_belong_to_income_tax(title):
    assert classify(title, "TW").key == "tw_income"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("所得稅法", "tw_income"),
        ("營利事業所得稅查核準則", "tw_profit_seeking"),
        ("綜合所得稅結算申報", "tw_individual_income"),
        ("加值型及非加值型營業稅法", "tw_business_tax"),
        ("稅捐稽徵法", "tw_collection"),
    ],
)
def test_the_new_keywords_do_not_steal_other_tax_types(title, expected):
    """扣繳 appears inside 所得稅法 too; the more specific types must still win."""
    assert classify(title, "TW").key == expected


class TestSourcesConfig:
    def test_the_innovation_act_is_collected(self):
        """第10條 研發投抵 and 第23-1～23-3 新創投資抵減 are filing obligations."""
        import yaml

        with open("config/sources.yaml", encoding="utf-8") as handle:
            config = yaml.safe_load(handle)
        categories = config["sources"]["tw-moj-law"]["config"]["law_categories"]
        assert "J0040051" in categories

    def test_every_category_is_a_plausible_pcode(self):
        """A typo fetches nothing and reports no error — the batch is just empty."""
        import re

        import yaml

        with open("config/sources.yaml", encoding="utf-8") as handle:
            config = yaml.safe_load(handle)
        for code in config["sources"]["tw-moj-law"]["config"]["law_categories"]:
            assert re.fullmatch(r"[A-Z]\d{7}", code), code
