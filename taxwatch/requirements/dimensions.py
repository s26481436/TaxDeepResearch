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

# taxpayer_class is always the party the tax is imposed on — never the party
# that merely files or withholds on someone else's behalf. Two runs of the same
# statute previously disagreed on the whole 信託 cluster (run A said `trustee`,
# run B said `beneficiary`) because the vocabulary offered both answers to one
# question with nothing to choose between them. The duty to file or withhold is
# already carried by `tax_scheme`, so it must not be encoded here as well.
_TW_INCOME_TAXPAYER_CLASSES = (
    DimensionValue("resident_individual", "中華民國境內居住之個人（居住者）"),
    DimensionValue("nonresident_individual", "非中華民國境內居住之個人（非居住者）"),
    DimensionValue("domestic_enterprise", "總機構在中華民國境內之營利事業"),
    DimensionValue("foreign_enterprise", "總機構在中華民國境外之營利事業"),
    DimensionValue("sole_proprietorship", "獨資、合夥組織之營利事業"),
    DimensionValue(
        "beneficiary",
        "信託行為之受益人",
        "信託財產發生之所得，依所得稅法第3條之4第1項歸屬受益人課稅時選用；"
        "此為信託所得的預設納稅主體。",
    ),
    DimensionValue(
        "trustee",
        "信託行為之受託人",
        "僅限受益人不特定或尚未存在，依所得稅法第3條之4第3項以受託人為納稅義務人者；"
        "此時 scenario_key 應填 beneficiary_unidentified 或 public_trust。"
        "受託人僅負代為計算、申報或扣繳義務時，不得選用本值。",
    ),
)

_TW_INCOME_TAX_SCHEMES = (
    DimensionValue("annual_filing", "結算申報／決算申報／清算申報"),
    DimensionValue(
        "withholding",
        "就源扣繳／扣繳申報",
        "本情境的規範內容是扣繳義務時選用。taxpayer_class 仍填所得歸屬人，"
        "不要填扣繳義務人。",
    ),
    DimensionValue("profit_distribution", "盈餘分配／未分配盈餘加徵"),
    DimensionValue("not_taxable", "免稅／不計入所得／不課稅"),
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
    "`taxpayer_class` 一律填「稅捐所歸屬的人」，不填代為申報或扣繳的人。"
    "扣繳義務人、代理人、負責人都不是 taxpayer_class 的合法值 —— "
    "那份義務由 `tax_scheme=withholding` 表達。",
    "信託所得預設 `taxpayer_class=beneficiary`；只有受益人不特定或尚未存在時"
    "（所得稅法第3條之4第3項）才填 `trustee`。",
    "若同一條文同時規範所得人與扣繳義務人，視為同一個情境，"
    "填所得人的 taxpayer_class 並以 `tax_scheme=withholding` 標示。",
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
