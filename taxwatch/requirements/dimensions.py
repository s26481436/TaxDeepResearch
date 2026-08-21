"""Controlled dimension vocabularies for stable TaxRequirement identity.

Dimensions and vocabularies are maintained per (country, tax_key).
Do not share or force-unify vocabularies across distinct tax regimes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DimensionValue:
    key: str
    label_zh: str
    description: str = ""


# Order of dimensions in identity_key:
# taxpayer_class | tax_scheme | subject_matter | scenario_key
DIMENSION_ORDER = (
    "taxpayer_class",
    "tax_scheme",
    "subject_matter",
    "scenario_key",
)

# Each dimension answers exactly one question. Where a dimension answered two,
# every row it touched had two defensible values and two runs picked different
# ones — the whole 信託 cluster swung between `trustee`, `beneficiary` and
# `resident_individual` across runs, because a trust beneficiary is genuinely
# all three: a role in a legal relationship, and a class of taxpayer.
#
# taxpayer_class answers only "what kind of entity is taxed" — residency and
# legal form. Roles in a particular relationship (受託人、受益人、扣繳義務人)
# belong to `scenario_key` and `tax_scheme`, which already carry them.
_TW_INCOME_TAXPAYER_CLASSES = (
    # Most provisions do not distinguish by taxpayer class at all — 房地合一,
    # 信託, and the withholding schedule apply to individuals and enterprises
    # alike. Without a value for that, the model had to name one of five
    # classes anyway, and two runs named different ones for the same row. An
    # answer the vocabulary cannot express gets invented.
    DimensionValue(
        "all_taxpayers",
        "不分主體類別（條文對各類納稅義務人一體適用）",
        "**預設值**。除非條文明確只適用某一類主體，或針對不同主體訂有不同規範，"
        "否則填本值。",
    ),
    DimensionValue("resident_individual", "中華民國境內居住之個人（居住者）"),
    DimensionValue("nonresident_individual", "非中華民國境內居住之個人（非居住者）"),
    DimensionValue("domestic_enterprise", "總機構在中華民國境內之營利事業"),
    DimensionValue("foreign_enterprise", "總機構在中華民國境外之營利事業"),
    DimensionValue("sole_proprietorship", "獨資、合夥組織之營利事業"),
)

# `not_taxable` answered a different question from the rest: the others say how
# the tax is collected, it says whether there is any. So every exemption could
# be filed as either 免稅 or 結算申報 with no way to choose, and 證券、期貨、
# 房地 each swung between the two. Whether an item is exempt is content of the
# row (`tax_base`, `formula`), not part of its identity.
_TW_INCOME_TAX_SCHEMES = (
    DimensionValue("annual_filing", "結算申報／決算申報／清算申報"),
    DimensionValue(
        "withholding",
        "就源扣繳／扣繳申報",
        "本情境的規範內容是扣繳義務時選用。taxpayer_class 仍填所得歸屬人，"
        "不要填扣繳義務人。",
    ),
    DimensionValue("profit_distribution", "盈餘分配／未分配盈餘加徵"),
)

_TW_INCOME_SUBJECT_MATTERS = (
    DimensionValue("general_income", "一般所得（綜合所得／營利事業所得）"),
    DimensionValue("real_estate", "房屋土地交易所得（房地合一稅）"),
    DimensionValue("securities", "證券交易所得"),
    DimensionValue("futures", "期貨交易所得"),
    DimensionValue("trust_income", "信託財產發生之所得"),
    DimensionValue("salary_interest", "各類扣繳所得（薪資、利息、股利等）"),
)

_TW_INCOME_SCENARIO_KEYS = (
    DimensionValue("standard", "標準／一般情境"),
    DimensionValue("post_2016_acquisition", "105年1月1日以後取得之房地交易（房地合一2.0）"),
    DimensionValue("presale_or_superficies", "預售屋買賣或設定地上權房地交易"),
    DimensionValue("indirect_shareholding", "符合特定條件之股權交易（視同房地交易）"),
    DimensionValue("beneficiary_identified", "受益人已確定存在"),
    DimensionValue("beneficiary_unidentified", "受益人不特定或尚未存在"),
    DimensionValue("public_trust", "公益信託"),
    DimensionValue("change_of_fiscal_year", "變更會計年度"),
    DimensionValue("loss_carryforward", "虧損扣除（前十年虧損互抵）"),
    DimensionValue("offshore_banking_unit", "國際金融業務分行（OBU）相關所得"),
)

# Rules that resolve choices the vocabulary alone leaves open. Every rule here
# exists because two runs of the same statute answered it differently.
_TW_INCOME_RULES = (
    "`taxpayer_class` 只回答「納稅的是什麼樣的主體」——居住者/非居住者、"
    "營利事業/個人。**不要填在法律關係中的角色**：受託人、受益人、"
    "扣繳義務人、代理人都不是合法值。",
    "條文若未依主體類別而有不同規範（房地合一、信託、各類所得扣繳多屬此類），"
    "`taxpayer_class` 填 `all_taxpayers`。**不要從五類主體中隨意挑一個** ——"
    "只有條文明確限定適用對象時，才填具體類別。",
    "信託所得填受益人本身的主體類別（通常是 `resident_individual` 或 "
    "`domestic_enterprise`），並以 `subject_matter=trust_income` 標示其為信託所得；"
    "受益人是否確定、是否為公益信託則由 `scenario_key` 表達"
    "（`beneficiary_identified`／`beneficiary_unidentified`／`public_trust`）。",
    "受益人不特定或尚未存在時以受託人為納稅義務人（所得稅法第3條之4第3項），"
    "此時 `scenario_key=beneficiary_unidentified`，`taxpayer_class` 仍填受託人的主體類別。",
    "`tax_scheme` 只回答「怎麼課、怎麼申報」，不回答「課不課」。"
    "免稅、停徵、不計入所得總額**不是** tax_scheme 的值——"
    "那屬於該列的 `tax_base` 或 `formula` 內容，仍依實際申報方式填 tax_scheme。",
    "若同一條文同時規範所得人與扣繳義務人，視為同一個情境，"
    "填所得人的 taxpayer_class 並以 `tax_scheme=withholding` 標示。",
    "**詞彙表中的非預設值就是「必須另成一列」的定義。** 條文若規範的標的"
    "對應到 `subject_matter` 的某個專門值（證券交易所得、期貨交易所得、"
    "房地交易所得、信託財產所得），必須使用該值並另成一列，"
    "不得併入 `general_income`。`general_income` 只用於條文未特別規範的一般所得。",
    "`scenario_key` 同理：條文若規範的是房地合一2.0、預售屋、視同房地交易、"
    "變更會計年度、虧損扣除、OBU 等特定情境，必須填對應的值並另成一列，"
    "不得填 `standard` 併入一般情境。`standard` 只用於沒有這些特別規定的情境。",
)

_RULES: dict[tuple[str, str], tuple[str, ...]] = {
    ("TW", "tw_income"): _TW_INCOME_RULES,
}


def get_identity_rules(country: str, tax_key: str) -> tuple[str, ...]:
    """Disambiguation rules for dimension choices in this tax regime."""
    return _RULES.get((country.upper(), tax_key), ())


_REGISTRY: dict[tuple[str, str], dict[str, tuple[DimensionValue, ...]]] = {
    ("TW", "tw_income"): {
        "taxpayer_class": _TW_INCOME_TAXPAYER_CLASSES,
        "tax_scheme": _TW_INCOME_TAX_SCHEMES,
        "subject_matter": _TW_INCOME_SUBJECT_MATTERS,
        "scenario_key": _TW_INCOME_SCENARIO_KEYS,
    },
}


def get_dimensions_vocabulary(country: str, tax_key: str) -> dict[str, tuple[DimensionValue, ...]]:
    """Return legal dimension values and explanations for (country, tax_key).

    Returns empty dict if no vocabulary is defined for the tax regime.
    """
    return _REGISTRY.get((country.upper(), tax_key), {})


def compute_identity_key(dimensions: dict[str, str] | None) -> str:
    """Build composite identity_key strictly following DIMENSION_ORDER.

    Requires ALL four dimensions to be non-empty. If any dimension is missing or empty,
    returns "" to prevent partial identity collisions and preserve safety.
    """
    if not dimensions:
        return ""
    values = [dimensions.get(k, "").strip() for k in DIMENSION_ORDER]
    if not all(values):
        return ""
    return "|".join(values)


def validate_dimensions(
    country: str,
    tax_key: str,
    raw_dimensions: dict[str, Any],
) -> tuple[dict[str, str], list[tuple[str, str]], list[str]]:
    """Validate dimension values against vocabulary.

    Returns:
        (valid_dimensions, unknown_values_list, missing_dimensions_list)
        - unknown_values_list: list of (dimension_name, received_invalid_value)
        - missing_dimensions_list: list of dimension_names that were empty/missing (only when some other dimension was present)
    """
    vocab = get_dimensions_vocabulary(country, tax_key)
    if not vocab:
        # Undefined vocabulary: return empty dimensions, no errors
        return {}, [], []

    valid_dims: dict[str, str] = {}
    unknowns: list[tuple[str, str]] = []
    missing: list[str] = []

    has_any_input = any(bool(str(raw_dimensions.get(d) or "").strip()) for d in DIMENSION_ORDER)

    for dim_name in DIMENSION_ORDER:
        val = str(raw_dimensions.get(dim_name) or "").strip()
        if not val:
            valid_dims[dim_name] = ""
            if has_any_input:
                missing.append(dim_name)
            continue
        allowed_keys = {item.key for item in vocab.get(dim_name, ())}
        if val in allowed_keys:
            valid_dims[dim_name] = val
        else:
            unknowns.append((dim_name, val))
            valid_dims[dim_name] = ""

    return valid_dims, unknowns, missing
