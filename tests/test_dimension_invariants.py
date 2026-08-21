"""Rules every regime's vocabulary must satisfy, asserted over all of them.

Each invariant here cost a round of extraction to discover on TW 所得稅. They
are properties of controlled identity, not of Taiwanese income tax, so a new
jurisdiction must not have to rediscover them:

- req-v7/v8: a dimension that answers two questions gives every row it touches
  two defensible values, and successive runs pick different ones.
- req-v9: a question with no legal answer for "the provision does not say" gets
  answered anyway, by invention.
- req-v10: a rule that only says what *not* to split leaves splitting optional,
  and coverage then varies run to run.
"""

from __future__ import annotations

import re

import pytest

from taxwatch.requirements.dimensions import (
    DIMENSION_ORDER,
    get_dimensions_vocabulary,
    get_identity_rules,
    registered_regimes,
)

REGIMES = registered_regimes()

# Parties that act on someone else's behalf. Whichever dimension carries the
# duty, it is not the one naming who is taxed.
ROLE_KEYS = {"withholding_agent", "trustee", "beneficiary", "agent", "payer", "filer"}

# "Is it taxed at all" is a different question from "how is it collected".
EXEMPTION_KEYS = {"not_taxable", "exempt", "tax_free", "no_tax", "zero_tax"}


def test_there_is_at_least_one_regime():
    assert REGIMES


@pytest.mark.parametrize("regime", REGIMES)
class TestEveryRegime:
    def test_defines_all_four_dimensions(self, regime):
        vocab = get_dimensions_vocabulary(*regime)
        for dimension in DIMENSION_ORDER:
            assert vocab.get(dimension), f"{regime} has no {dimension} values"

    def test_taxpayer_class_offers_a_value_for_provisions_that_name_no_class(self, regime):
        """Otherwise the model picks one of the specific classes at random."""
        classes = get_dimensions_vocabulary(*regime)["taxpayer_class"]
        assert classes[0].key == "all_taxpayers", (
            f"{regime}: the general case must exist and be offered first"
        )
        assert "預設" in classes[0].description

    def test_scenario_key_offers_a_general_case(self, regime):
        keys = {v.key for v in get_dimensions_vocabulary(*regime)["scenario_key"]}
        assert "standard" in keys

    def test_no_role_is_offered_as_a_taxpayer_class(self, regime):
        keys = {v.key for v in get_dimensions_vocabulary(*regime)["taxpayer_class"]}
        assert keys.isdisjoint(ROLE_KEYS), f"{regime}: {keys & ROLE_KEYS} name a role, not a class"

    def test_exemption_is_not_offered_as_a_collection_method(self, regime):
        keys = {v.key for v in get_dimensions_vocabulary(*regime)["tax_scheme"]}
        assert keys.isdisjoint(EXEMPTION_KEYS), f"{regime}: {keys & EXEMPTION_KEYS}"

    def test_keys_are_unique_within_each_dimension(self, regime):
        vocab = get_dimensions_vocabulary(*regime)
        for dimension in DIMENSION_ORDER:
            keys = [v.key for v in vocab[dimension]]
            assert len(keys) == len(set(keys)), f"{regime}.{dimension} repeats a key"

    def test_keys_are_ascii_snake_case(self, regime):
        """identity_key is a joined string; a key with `|` or a space breaks it."""
        vocab = get_dimensions_vocabulary(*regime)
        for dimension in DIMENSION_ORDER:
            for value in vocab[dimension]:
                assert re.fullmatch(r"[a-z][a-z0-9_]*", value.key), (
                    f"{regime}.{dimension}: {value.key!r}"
                )

    def test_every_value_is_labelled(self, regime):
        vocab = get_dimensions_vocabulary(*regime)
        for dimension in DIMENSION_ORDER:
            for value in vocab[dimension]:
                assert value.label_zh.strip(), f"{regime}.{dimension}.{value.key} has no label"

    def test_has_disambiguation_rules(self, regime):
        assert get_identity_rules(*regime), (
            f"{regime}: a vocabulary alone leaves choices open; state how to make them"
        )

    def test_the_rules_say_not_to_guess_a_specific_class(self, regime):
        rules = " ".join(get_identity_rules(*regime))
        assert "all_taxpayers" in rules

    def test_the_rules_say_what_must_be_split(self, regime):
        """Saying only what not to split leaves coverage varying between runs."""
        rules = " ".join(get_identity_rules(*regime))
        assert "另成一列" in rules


def test_regimes_do_not_share_a_vocabulary_object():
    """台灣與中國是不同的課稅主體。Shared values would look comparable and not be."""
    seen: dict[int, tuple[str, str]] = {}
    for regime in REGIMES:
        for values in get_dimensions_vocabulary(*regime).values():
            assert id(values) not in seen, f"{regime} reuses {seen[id(values)]}'s values"
            seen[id(values)] = regime
