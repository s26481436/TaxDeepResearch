"""Tax type taxonomy — classify documents into canonical tax categories.

A document title such as 「中华人民共和国企业所得税法实施条例」 or 「所得稅法」
maps to a single canonical tax type so the dashboard can group法規 by 稅種.

Order matters: the first matching entry wins, so more specific categories
(環境保護稅) must precede broader ones (稅捐稽徵).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TaxType:
    key: str
    name_zh: str
    keywords: tuple[str, ...]


# Ordered most-specific first.
TAX_TYPES: tuple[TaxType, ...] = (
    TaxType(
        "enterprise_income",
        "企業所得稅",
        (
            "企业所得税",
            "企業所得稅",
            "營利事業所得稅",
            "营利事业所得税",
        ),
    ),
    TaxType(
        "individual_income",
        "個人所得稅",
        (
            "个人所得税",
            "個人所得稅",
            "综合所得税",
            "綜合所得稅",
        ),
    ),
    TaxType("income", "所得稅", ("所得税", "所得稅")),
    # 土地增值稅 must be matched before 增值稅, or the substring 增值税 inside
    # it wins and a land-value-increment document is filed under VAT.
    TaxType(
        "property",
        "房屋稅／土地稅",
        (
            "土地增值税",
            "土地增值稅",
            "房屋税",
            "房屋稅",
            "土地税",
            "土地稅",
            "契税",
            "契稅",
            "房产税",
            "房產稅",
            "城镇土地使用税",
            "城鎮土地使用稅",
            "耕地占用税",
            "耕地占用稅",
        ),
    ),
    TaxType(
        "vat",
        "增值稅／營業稅",
        (
            "增值税",
            "增值稅",
            "營業稅",
            "营业税",
            "加值型",
            "非加值型",
        ),
    ),
    TaxType("stamp", "印花稅", ("印花税", "印花稅")),
    TaxType(
        "environmental",
        "環境保護稅",
        (
            "环境保护税",
            "環境保護稅",
            "环保税",
            "環保稅",
        ),
    ),
    TaxType("resource", "資源稅", ("资源税", "資源稅")),
    TaxType(
        "urban_maintenance",
        "城市維護建設稅",
        (
            "城市维护建设税",
            "城市維護建設稅",
        ),
    ),
    TaxType(
        "consumption",
        "消費稅／貨物稅",
        (
            "消费税",
            "消費稅",
            "貨物稅",
            "货物税",
            "特種貨物",
            "特种货物",
        ),
    ),
    TaxType(
        "estate_gift",
        "遺產及贈與稅",
        (
            "遗产税",
            "遺產稅",
            "赠与税",
            "贈與稅",
            "遺產及贈與",
            "遗产及赠与",
        ),
    ),
    TaxType(
        "vehicle",
        "車輛稅",
        (
            "车辆购置税",
            "車輛購置稅",
            "使用牌照稅",
            "车船税",
            "車船稅",
        ),
    ),
    TaxType(
        "tobacco_alcohol",
        "菸酒稅",
        (
            "菸酒稅",
            "烟酒税",
            "烟叶税",
            "菸葉稅",
        ),
    ),
    TaxType("securities", "證券交易稅", ("证券交易税", "證券交易稅")),
    TaxType("customs", "關稅", ("关税", "關稅")),
    # Deliberately last: 征管 documents are about the *process* (申報, 發票,
    # 登記, 稽查) and frequently name a specific tax in passing. Matching it
    # ahead of the specific taxes was measured to steal 6% from them, so it
    # runs only as a fallback for what would otherwise be unclassified.
    TaxType(
        "collection",
        "稅捐稽徵",
        (
            "税收征收管理",
            "稅捐稽徵",
            "征管法",
            "徵管法",
            "税务登记",
            "稅務登記",
            "纳税申报",
            "納稅申報",
            "申报表",
            "申報表",
            "代扣代缴",
            "代扣代繳",
            "发票",
            "發票",
            "征管",
            "徵管",
            "稽查",
            "涉税",
            "涉稅",
            "纳税信用",
            "納稅信用",
            "税收违法",
            "稅收違法",
            "欠税",
            "欠稅",
            "催缴",
            "催繳",
            "纳税人识别",
            "納稅人識別",
            "税务行政",
            "稅務行政",
        ),
    ),
)

UNCLASSIFIED = TaxType("other", "其他稅務規定", ())


def classify(text: str) -> TaxType:
    """Classify a document title (or provision key) into a tax type."""
    if not text:
        return UNCLASSIFIED
    for tax_type in TAX_TYPES:
        for keyword in tax_type.keywords:
            if keyword in text:
                return tax_type
    return UNCLASSIFIED


def by_key(key: str) -> TaxType | None:
    """Look up a tax type by its stable key."""
    if key == UNCLASSIFIED.key:
        return UNCLASSIFIED
    for tax_type in TAX_TYPES:
        if tax_type.key == key:
            return tax_type
    return None
