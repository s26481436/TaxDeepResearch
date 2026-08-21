"""A controlled vocabulary that admits two valid answers cannot stabilise identity.

Two dry runs of `tw_income` agreed on 17 identities and disagreed on 10. The
disagreement was not diffuse: one run assigned the whole 信託 cluster to
`trustee`, the other to `beneficiary`, and the 扣繳 rows swung between the
withholding agent and the income earner. Both readings were defensible, because
the vocabulary listed the filing party and the taxed party side by side under
one dimension with nothing to choose between them.
"""

from __future__ import annotations

from taxwatch.requirements.dimensions import (
    get_dimensions_vocabulary,
    get_identity_rules,
    validate_dimensions,
)
from taxwatch.requirements.prompts import format_dimensions_section


def _classes() -> set[str]:
    return {v.key for v in get_dimensions_vocabulary("TW", "tw_income")["taxpayer_class"]}


def test_the_filing_party_is_not_a_taxpayer_class():
    """扣繳義務人 files on another's behalf; `tax_scheme=withholding` says so."""
    assert "withholding_agent" not in _classes()


def test_a_retired_value_is_reported_rather_than_silently_accepted():
    _, unknowns, _ = validate_dimensions(
        "TW", "tw_income", {"taxpayer_class": "withholding_agent"}
    )
    assert ("taxpayer_class", "withholding_agent") in unknowns


def test_trustee_survives_because_the_statute_sometimes_taxes_it():
    """所得稅法第3條之4第3項 does make the trustee the taxpayer."""
    assert "trustee" in _classes()
    trustee = next(
        v
        for v in get_dimensions_vocabulary("TW", "tw_income")["taxpayer_class"]
        if v.key == "trustee"
    )
    assert "不特定" in trustee.description, "the narrow case must be stated, not implied"


def test_the_trust_default_is_written_down():
    rules = " ".join(get_identity_rules("TW", "tw_income"))
    assert "beneficiary" in rules and "trustee" in rules


def test_rules_reach_the_prompt():
    section = format_dimensions_section("TW", "tw_income")
    assert "維度選擇規則" in section
    for rule in get_identity_rules("TW", "tw_income"):
        assert rule in section


def test_no_rules_section_without_a_vocabulary():
    assert format_dimensions_section("CN", "cn_vat") == ""
