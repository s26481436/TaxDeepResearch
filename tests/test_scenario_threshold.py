"""What counts as a 課稅情境, and what merely fills a column of one.

Extracting 所得稅法 produced 349 scenarios. The head was right — 個人（境內
居住者）, 營利事業（總機構在境內）, 信託受益人, 房地合一 — but the tail had
turned every provision of the 查核準則 into a row of its own:

    營利事業商品盤損認列規範
    營利事業災害損失認列規範
    前期損益調整之帳外調整與列報

Those have no taxpayer, no rate and no deadline of their own; they say how one
deduction is recognised inside 營利事業所得稅結算申報. Rows like that cannot
fill the other ten columns, which is where the 「條文未明定」 cells came from.

The prompt taught the model how to split and never what not to split.
"""

from __future__ import annotations

import pytest

from taxwatch.requirements.prompts import EXTRACTION_TEMPLATE, PROMPT_VERSION, SYSTEM_PROMPT


def test_version_is_bumped():
    assert PROMPT_VERSION == "req-v4"


def test_a_scenario_is_defined_by_taxpayer_object_and_rate():
    assert "納稅義務人、課稅標的與稅率" in SYSTEM_PROMPT


@pytest.mark.parametrize(
    "kind,field",
    [
        ("盤損", "deductions"),
        ("報廢", "deductions"),
        ("災害損失", "deductions"),
        ("核課期間", "administration"),
        ("滯納金", "administration"),
        ("罰鍰", "administration"),
    ],
)
def test_named_kinds_are_routed_to_a_column_not_a_row(kind, field):
    """Each was seen as a spurious row in a real run."""
    assert kind in SYSTEM_PROMPT
    assert field in SYSTEM_PROMPT


def test_supplements_enrich_scenarios_rather_than_create_them():
    """子法 provisions are detail; the consolidated view is full of them."""
    assert "不是用來新增情境的" in SYSTEM_PROMPT


def test_one_row_per_provision_is_called_out_as_wrong():
    assert "一條條文對應一列" in SYSTEM_PROMPT


def test_user_prompt_repeats_the_rule():
    """The system prompt sets policy; the task template must not contradict it."""
    assert "不另成列" in EXTRACTION_TEMPLATE


def test_earlier_rules_survive_the_renumbering():
    for anchor in ("只寫條文支持的內容", "node_key 必須逐字取自輸入", "逐字照抄條文"):
        assert anchor in SYSTEM_PROMPT
