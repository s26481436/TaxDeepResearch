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
    country: str
    keywords: tuple[str, ...]


# Ordered most-specific first per country.
TAX_TYPES: tuple[TaxType, ...] = (
    # ==================== CN (中國大陸) ====================
    TaxType(
        "cn_enterprise_income",
        "企業所得稅",
        "CN",
        (
            "企业所得税",
            "企業所得稅",
        ),
    ),
    TaxType(
        "cn_individual_income",
        "個人所得稅",
        "CN",
        (
            "个人所得税",
            "個人所得稅",
        ),
    ),
    TaxType(
        "cn_income",
        "所得稅",
        "CN",
        (
            "所得税",
            "所得稅",
        ),
    ),
    # 土地增值税 must be matched before 增值税
    TaxType(
        "cn_property",
        "房產稅／土地稅",
        "CN",
        (
            "土地增值税",
            "土地增值稅",
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
        "cn_vat",
        "增值稅",
        "CN",
        (
            "增值税",
            "增值稅",
            "营业税",
            "營業稅",
        ),
    ),
    TaxType(
        "cn_stamp",
        "印花稅",
        "CN",
        (
            "印花税",
            "印花稅",
        ),
    ),
    TaxType(
        "cn_environmental",
        "環境保護稅",
        "CN",
        (
            "环境保护税",
            "環境保護稅",
            "环保税",
            "環保稅",
        ),
    ),
    TaxType(
        "cn_resource",
        "資源稅",
        "CN",
        (
            "资源税",
            "資源稅",
        ),
    ),
    TaxType(
        "cn_urban_maintenance",
        "城市維護建設稅",
        "CN",
        (
            "城市维护建设税",
            "城市維護建設稅",
        ),
    ),
    TaxType(
        "cn_consumption",
        "消費稅",
        "CN",
        (
            "消费税",
            "消費稅",
        ),
    ),
    TaxType(
        "cn_estate_gift",
        "遺產及贈與稅",
        "CN",
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
        "cn_vehicle",
        "車輛稅",
        "CN",
        (
            "车辆购置税",
            "車輛購置稅",
            "车船税",
            "車船稅",
        ),
    ),
    TaxType(
        "cn_tobacco",
        "煙葉稅",
        "CN",
        (
            "烟叶税",
            "菸葉稅",
            "烟草",
            "菸草",
        ),
    ),
    TaxType(
        "cn_securities",
        "證券交易稅",
        "CN",
        (
            "证券交易税",
            "證券交易稅",
        ),
    ),
    TaxType(
        "cn_customs",
        "關稅",
        "CN",
        (
            "关税",
            "關稅",
            "进出口税收",
            "進出口稅收",
        ),
    ),
    TaxType(
        "cn_collection",
        "稅收征管",
        "CN",
        (
            "税收征收管理",
            "稅收征收管理",
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
    # ==================== TW (台灣) ====================
    TaxType(
        "tw_profit_seeking",
        "營利事業所得稅",
        "TW",
        (
            "營利事業所得稅",
            "营利事业所得税",
            "所得基本稅額",
            "所得基本税额",
        ),
    ),
    TaxType(
        "tw_individual_income",
        "綜合所得稅",
        "TW",
        (
            "綜合所得稅",
            "综合所得税",
            "個人所得稅",
            "个人所得税",
        ),
    ),
    TaxType(
        "tw_income",
        "所得稅",
        "TW",
        (
            "所得稅",
            "所得税",
            # 扣繳 is a way of collecting 所得稅, not a tax of its own — the same
            # call the requirement dimensions make, where it is a `tax_scheme`
            # rather than a taxpayer or a tax type. Without these,
            # 各類所得扣繳率標準 matches nothing (its title says 所得, never
            # 所得稅) and lands in tw_other, out of reach of the 所得稅 matrix
            # whose rates it sets.
            "扣繳",
            "扣缴",
        ),
    ),
    # 土地增值稅 must be matched before 房屋稅 / 營業稅
    TaxType(
        "tw_property",
        "房屋稅／土地稅",
        "TW",
        (
            "土地增值稅",
            "土地增值税",
            "房屋稅",
            "房屋税",
            "土地稅",
            "土地税",
            "地價稅",
            "地价税",
            "契稅",
            "契税",
            "田賦",
            "田赋",
        ),
    ),
    TaxType(
        "tw_business_tax",
        "營業稅",
        "TW",
        (
            "營業稅",
            "营业税",
            "加值型",
            "非加值型",
        ),
    ),
    TaxType(
        "tw_stamp",
        "印花稅",
        "TW",
        (
            "印花稅",
            "印花税",
        ),
    ),
    TaxType(
        "tw_commodity",
        "貨物稅",
        "TW",
        (
            "貨物稅",
            "货物税",
            "特種貨物",
            "特种货物",
            "消費稅",
            "消费税",
        ),
    ),
    TaxType(
        "tw_estate_gift",
        "遺產及贈與稅",
        "TW",
        (
            "遺產稅",
            "遗产税",
            "贈與稅",
            "赠与税",
            "遺產及贈與",
            "遗产及赠与",
        ),
    ),
    TaxType(
        "tw_vehicle",
        "使用牌照稅",
        "TW",
        (
            "使用牌照稅",
            "使用牌照税",
            "牌照稅",
            "牌照税",
            "車輛",
            "车辆",
        ),
    ),
    TaxType(
        "tw_tobacco_alcohol",
        "菸酒稅",
        "TW",
        (
            "菸酒稅",
            "烟酒税",
            "菸酒",
            "烟酒",
        ),
    ),
    TaxType(
        "tw_securities",
        "證券交易稅",
        "TW",
        (
            "證券交易稅",
            "证券交易税",
            "期貨交易稅",
            "期货交易税",
        ),
    ),
    TaxType(
        "tw_customs",
        "關稅",
        "TW",
        (
            "關稅",
            "关税",
            "海關",
            "海关",
        ),
    ),
    TaxType(
        "tw_collection",
        "稅捐稽徵",
        "TW",
        (
            "稅捐稽徵",
            "税捐稽征",
            "稽徵",
            "稽征",
            "稅務違章",
            "税务违章",
            "統一發票",
            "统一发票",
        ),
    ),
    # ==================== US (美國) ====================
    TaxType(
        "us_income",
        "Income Tax",
        "US",
        (
            "income tax",
            "corporate tax",
            "individual tax",
        ),
    ),
)

_UNCLASSIFIED_MAP: dict[str, TaxType] = {
    "CN": TaxType("cn_other", "其他稅務規定", "CN", ()),
    "TW": TaxType("tw_other", "其他稅務規定", "TW", ()),
    "US": TaxType("us_other", "Other Tax Provisions", "US", ()),
}

UNCLASSIFIED = TaxType("other", "其他稅務規定", "", ())


def unclassified_for(country: str) -> TaxType:
    """Return the unclassified TaxType for a specific country."""
    c = (country or "").upper()
    return _UNCLASSIFIED_MAP.get(c, TaxType(f"{c.lower()}_other" if c else "other", "其他稅務規定", c, ()))


def classify(text: str, country: str) -> TaxType:
    """Classify a document title (or provision key) into a tax type for a given country."""
    c = (country or "").upper()
    if not text:
        return unclassified_for(c)
    for tax_type in TAX_TYPES:
        if tax_type.country.upper() == c:
            for keyword in tax_type.keywords:
                if keyword in text:
                    return tax_type
    return unclassified_for(c)


def by_key(key: str) -> TaxType | None:
    """Look up a tax type by its stable key."""
    if key == UNCLASSIFIED.key:
        return UNCLASSIFIED
    for unclass in _UNCLASSIFIED_MAP.values():
        if unclass.key == key:
            return unclass
    for tax_type in TAX_TYPES:
        if tax_type.key == key:
            return tax_type
    return None


def by_country(country: str) -> tuple[TaxType, ...]:
    """List all tax types for a given country."""
    c = (country or "").upper()
    return tuple(t for t in TAX_TYPES if t.country.upper() == c)
