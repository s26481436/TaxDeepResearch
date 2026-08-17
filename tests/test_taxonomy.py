"""Tests for tax type classification."""

from taxwatch.taxonomy import (
    TAX_TYPES,
    UNCLASSIFIED,
    by_country,
    by_key,
    classify,
    unclassified_for,
)


def test_classify_cn_enterprise_income():
    assert classify("中华人民共和国企业所得税法", "CN").key == "cn_enterprise_income"
    assert classify("中华人民共和国企业所得税法实施条例", "CN").key == "cn_enterprise_income"


def test_classify_tw_profit_seeking():
    assert classify("營利事業所得稅查核準則", "TW").key == "tw_profit_seeking"


def test_enterprise_income_beats_generic_income():
    """The specific 企業所得稅 entry must win over the broader 所得稅 entry."""
    assert classify("企业所得税法", "CN").key == "cn_enterprise_income"
    assert classify("所得税法", "CN").key == "cn_income"
    assert classify("所得稅法", "TW").key == "tw_income"


def test_classify_vat_and_business_tax_are_distinct():
    assert classify("增值税暂行条例", "CN").key == "cn_vat"
    assert classify("加值型及非加值型營業稅法", "TW").key == "tw_business_tax"


def test_classify_cn_specific_taxes():
    assert classify("中华人民共和国环境保护税法", "CN").key == "cn_environmental"
    assert classify("中华人民共和国资源税法", "CN").key == "cn_resource"
    assert classify("城市维护建设税法", "CN").key == "cn_urban_maintenance"
    assert classify("中华人民共和国印花税法", "CN").key == "cn_stamp"
    assert classify("土地增值税暂行条例", "CN").key == "cn_property"
    assert classify("烟叶税法", "CN").key == "cn_tobacco"


def test_classify_tw_taxes():
    assert classify("遺產及贈與稅法", "TW").key == "tw_estate_gift"
    assert classify("房屋稅條例", "TW").key == "tw_property"
    assert classify("貨物稅條例", "TW").key == "tw_commodity"
    assert classify("特種貨物及勞務稅條例", "TW").key == "tw_commodity"
    assert classify("使用牌照稅法", "TW").key == "tw_vehicle"
    assert classify("菸酒稅法", "TW").key == "tw_tobacco_alcohol"
    assert classify("證券交易稅條例", "TW").key == "tw_securities"
    assert classify("關稅法", "TW").key == "tw_customs"
    assert classify("稅捐稽徵法", "TW").key == "tw_collection"


def test_classify_unknown():
    assert classify("某個不相關的公告", "CN").key == "cn_other"
    assert classify("", "CN").key == "cn_other"
    assert classify("某個不相關的公告", "TW").key == "tw_other"
    assert classify("", "TW").key == "tw_other"


def test_by_key_roundtrip():
    assert by_key("cn_enterprise_income").name_zh == "企業所得稅"
    assert by_key("tw_business_tax").name_zh == "營業稅"
    assert by_key(UNCLASSIFIED.key) is UNCLASSIFIED
    assert by_key("cn_other").key == "cn_other"
    assert by_key("tw_other").key == "tw_other"
    assert by_key("no_such_key") is None


def test_by_country():
    cn_types = by_country("CN")
    tw_types = by_country("TW")
    assert all(t.country == "CN" for t in cn_types)
    assert all(t.country == "TW" for t in tw_types)
    assert any(t.key == "cn_vat" for t in cn_types)
    assert any(t.key == "tw_business_tax" for t in tw_types)


def test_unclassified_for():
    assert unclassified_for("CN").key == "cn_other"
    assert unclassified_for("TW").key == "tw_other"
    assert unclassified_for("US").key == "us_other"
