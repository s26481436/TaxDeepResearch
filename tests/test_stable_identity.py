"""Tests for Stage 2: Stable Requirement Identity."""

from __future__ import annotations

import pytest

from taxwatch.requirements.dimensions import (
    compute_identity_key,
    get_dimensions_vocabulary,
    validate_dimensions,
)


def test_vocabulary_per_tax_regime():
    """1. 詞彙表以 (country, tax_key) 為單位，未定義的稅種回傳空."""
    vocab_tw = get_dimensions_vocabulary("TW", "tw_income")
    assert "taxpayer_class" in vocab_tw
    assert "tax_scheme" in vocab_tw
    assert "subject_matter" in vocab_tw
    assert "scenario_key" in vocab_tw

    vocab_cn = get_dimensions_vocabulary("CN", "cn_vat")
    assert vocab_cn == {}

    vocab_unknown = get_dimensions_vocabulary("US", "us_cit")
    assert vocab_unknown == {}


def test_identity_key_fixed_order():
    """2. identity_key 由四維度依固定順序組成，順序不因輸入順序改變."""
    dims1 = {
        "taxpayer_class": "resident_individual",
        "tax_scheme": "annual_filing",
        "subject_matter": "general_income",
        "scenario_key": "standard",
    }
    dims2 = {
        "scenario_key": "standard",
        "subject_matter": "general_income",
        "taxpayer_class": "resident_individual",
        "tax_scheme": "annual_filing",
    }
    expected = "resident_individual|annual_filing|general_income|standard"
    assert compute_identity_key(dims1) == expected
    assert compute_identity_key(dims2) == expected


def test_identity_key_empty_when_all_empty():
    """3. 四維度皆空時 identity_key 為空."""
    assert compute_identity_key({}) == ""
    assert compute_identity_key(None) == ""
    assert (
        compute_identity_key(
            {
                "taxpayer_class": "",
                "tax_scheme": "",
                "subject_matter": "",
                "scenario_key": "",
            }
        )
        == ""
    )


@pytest.mark.parametrize(
    "missing_field",
    ["taxpayer_class", "tax_scheme", "subject_matter", "scenario_key"],
)
def test_identity_key_empty_when_any_dimension_missing(missing_field):
    """任一維度為空 → identity_key 為空（防止部分身分碰撞）."""
    full = {
        "taxpayer_class": "resident_individual",
        "tax_scheme": "annual_filing",
        "subject_matter": "general_income",
        "scenario_key": "standard",
    }
    full[missing_field] = ""
    assert compute_identity_key(full) == ""


def test_validate_dimensions_distinguishes_missing_and_invalid():
    """驗證 validate_dimensions 可區分「值不合法」與「值缺漏」兩種情況."""
    raw = {
        "taxpayer_class": "resident_individual",  # Valid
        "tax_scheme": "invalid_scheme",           # Invalid
        "subject_matter": "",                     # Missing
        "scenario_key": "standard",               # Valid
    }
    valid_dims, unknowns, missing = validate_dimensions("TW", "tw_income", raw)

    assert valid_dims["taxpayer_class"] == "resident_individual"
    assert valid_dims["scenario_key"] == "standard"
    assert valid_dims["tax_scheme"] == ""
    assert valid_dims["subject_matter"] == ""

    assert unknowns == [("tax_scheme", "invalid_scheme")]
    assert missing == ["subject_matter"]
    # identity_key must be empty due to incomplete valid dimensions
    assert compute_identity_key(valid_dims) == ""
