"""Prompt contract test — every JSON key the schema expects must appear in the prompt.

This is the regression guard for Bug 2: the model can't output keys it was
never told about, and pydantic silently defaults missing keys to [].
"""

from __future__ import annotations

from taxwatch.requirements.prompts import EXTRACTION_TEMPLATE, SYSTEM_PROMPT
from taxwatch.requirements.schema import (
    ProvisionCitation,
    RequirementFieldOut,
    RequirementOut,
    RequirementSetOut,
)


def _all_field_names(*models):
    names = set()
    for model in models:
        for name in model.model_fields:
            names.add(name)
    return names


def test_all_schema_keys_appear_in_prompt():
    prompt_text = SYSTEM_PROMPT + EXTRACTION_TEMPLATE
    missing = []
    for name in _all_field_names(
        RequirementSetOut,
        RequirementOut,
        RequirementFieldOut,
        ProvisionCitation,
    ):
        if name not in prompt_text:
            missing.append(name)
    assert not missing, f"Schema keys missing from prompt: {missing}"
