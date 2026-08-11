"""Tests for tax type classification."""
from taxwatch.taxonomy import UNCLASSIFIED, by_key, classify


def test_classify_cn_enterprise_income():
    assert classify("中华人民共和国企业所得税法").key == "enterprise_income"
    assert classify("中华人民共和国企业所得税法实施条例").key == "enterprise_income"


def test_classify_tw_profit_seeking():
    assert classify("營利事業所得稅查核準則").key == "enterprise_income"


def test_enterprise_income_beats_generic_income():
    """The specific 企業所得稅 entry must win over the broader 所得稅 entry."""
    assert classify("企业所得税法").key == "enterprise_income"
    assert classify("所得稅法").key == "income"


def test_classify_vat_both_scripts():
    assert classify("增值税暂行条例").key == "vat"
    assert classify("加值型及非加值型營業稅法").key == "vat"


def test_classify_cn_specific_taxes():
    assert classify("中华人民共和国环境保护税法").key == "environmental"
    assert classify("中华人民共和国资源税法").key == "resource"
    assert classify("城市维护建设税法").key == "urban_maintenance"
    assert classify("中华人民共和国印花税法").key == "stamp"


def test_classify_tw_taxes():
    assert classify("遺產及贈與稅法").key == "estate_gift"
    assert classify("房屋稅條例").key == "property"
    assert classify("特種貨物及勞務稅條例").key == "consumption"
    assert classify("稅捐稽徵法").key == "collection"


def test_classify_unknown():
    assert classify("某個不相關的公告").key == UNCLASSIFIED.key
    assert classify("").key == UNCLASSIFIED.key


def test_by_key_roundtrip():
    assert by_key("enterprise_income").name_zh == "企業所得稅"
    assert by_key(UNCLASSIFIED.key) is UNCLASSIFIED
    assert by_key("no_such_key") is None
