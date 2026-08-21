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


def test_a_role_is_not_a_taxpayer_class():
    """A trust beneficiary is genuinely a role *and* a resident individual.

    Offering both under one dimension is what made the whole 信託 cluster swing
    between `trustee`, `beneficiary` and `resident_individual` across runs.
    """
    assert _classes().isdisjoint({"trustee", "beneficiary"})


def test_a_provision_that_names_no_class_has_a_value_to_say_so():
    """房地合一 and 信託 apply to individuals and enterprises alike.

    With no value for that, the model named one of five classes anyway and two
    runs named different ones for the same row.
    """
    assert "all_taxpayers" in _classes()


def test_the_general_case_is_the_first_value_offered():
    classes = get_dimensions_vocabulary("TW", "tw_income")["taxpayer_class"]
    assert classes[0].key == "all_taxpayers"
    assert "預設" in classes[0].description


def test_not_choosing_arbitrarily_is_written_into_the_rules():
    rules = " ".join(get_identity_rules("TW", "tw_income"))
    assert "all_taxpayers" in rules


def test_taxpayer_class_covers_only_residency_and_legal_form():
    assert _classes() == {
        "all_taxpayers",
        "resident_individual",
        "nonresident_individual",
        "domestic_enterprise",
        "foreign_enterprise",
        "sole_proprietorship",
    }


def test_the_trust_roles_moved_to_scenario_key():
    """Removing them from taxpayer_class must not lose the distinction."""
    keys = {v.key for v in get_dimensions_vocabulary("TW", "tw_income")["scenario_key"]}
    assert {"beneficiary_identified", "beneficiary_unidentified", "public_trust"} <= keys


def test_exemption_is_not_a_collection_method():
    """`tax_scheme` says how the tax is collected, not whether there is one."""
    schemes = {v.key for v in get_dimensions_vocabulary("TW", "tw_income")["tax_scheme"]}
    assert "not_taxable" not in schemes
    assert schemes == {"annual_filing", "withholding", "profit_distribution"}


def test_the_retired_values_are_named_in_the_rules():
    """A value the model used last run must be ruled out explicitly."""
    rules = " ".join(get_identity_rules("TW", "tw_income"))
    for term in ("受託人", "受益人", "扣繳義務人", "免稅"):
        assert term in rules


def test_rules_reach_the_prompt():
    section = format_dimensions_section("TW", "tw_income")
    assert "維度選擇規則" in section
    for rule in get_identity_rules("TW", "tw_income"):
        assert rule in section


def test_no_rules_section_without_a_vocabulary():
    assert format_dimensions_section("CN", "cn_vat") == ""
