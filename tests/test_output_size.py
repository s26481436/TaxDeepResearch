"""The extraction's output must not restate what the caller already holds.

An input of 3,664 tokens was answering with 16,384 — the cap — and taking four
to five minutes per batch. Output size is paid per cell per row, and the matrix
has eleven cells, so anything redundant in a citation is multiplied by a
hundred before the batch ends.
"""

from __future__ import annotations

import json

from taxwatch.requirements.fields import FIELD_KEYS
from taxwatch.requirements.prompts import SYSTEM_PROMPT
from taxwatch.requirements.schema import ProvisionCitation


def test_citation_does_not_ask_for_the_law_name():
    """It is node_key up to the '#'; the model repeating it buys nothing."""
    assert "title" not in ProvisionCitation.model_fields


def test_quote_asks_for_a_locator_not_the_provision():
    description = ProvisionCitation.model_fields["quote"].description or ""
    assert "20" in description
    assert "全文" in description or "整條" in description


def test_stored_quote_is_trimmed_even_if_the_model_ignores_the_limit(tmp_path):
    from taxwatch.requirements.extract import _MAX_QUOTE_CHARS, _verify_citations

    long_quote = "薪" * 500
    kept, dropped = _verify_citations(
        [ProvisionCitation(node_key="所得稅法#24", quote=long_quote)],
        {"所得稅法#24"},
    )

    assert dropped == 0
    assert len(kept[0]["quote"]) == _MAX_QUOTE_CHARS
    assert kept[0]["title"] == "所得稅法", "law name is derived, not stored blindly"


def test_prompt_tells_the_model_to_omit_underivable_fields():
    """A missing cell is already rendered as missing; restating it is waste."""
    assert "省略" in SYSTEM_PROMPT


def test_omitting_underivable_fields_is_the_dominant_saving():
    """Guards the reasoning behind the prompt change, in the units that matter."""

    def scenario(known: int, quote_len: int, with_title: bool, omit: bool):
        fields = []
        for i, key in enumerate(FIELD_KEYS):
            if i < known:
                citation = {"node_key": "所得稅法#24", "quote": "薪" * quote_len}
                if with_title:
                    citation["title"] = "所得稅法"
                fields.append(
                    {"field_key": key, "value": "應核實認列" * 4, "citations": [citation],
                     "confidence": 0.9}
                )
            elif not omit:
                fields.append(
                    {"field_key": key, "value": "條文未明定，待人工補充", "citations": [],
                     "confidence": 0.0}
                )
        return {"scenario": "一般申報", "taxpayer_role": "營利事業", "fields": fields}

    def size(known, quote_len, with_title, omit):
        rows = [scenario(known, quote_len, with_title, omit) for _ in range(10)]
        return len(json.dumps({"requirements": rows, "unresolved": []}, ensure_ascii=False))

    before = size(3, 60, True, omit=False)
    after = size(3, 20, False, omit=True)

    assert after < before / 2, f"{before} -> {after}"
