"""Tests for the LLM client structured output handling."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from taxwatch.requirements.schema import RequirementSetOut


def test_missing_requirements_key_raises():
    """If the LLM omits 'requirements', pydantic must raise, not default to []."""
    with pytest.raises(ValidationError):
        RequirementSetOut.model_validate({"unresolved": ["something"]})


def test_valid_requirements_parses():
    data = {
        "requirements": [
            {
                "scenario": "一般貨物銷售",
                "taxpayer_role": "一般納稅人",
                "fields": [],
            }
        ],
        "unresolved": [],
    }
    result = RequirementSetOut.model_validate(data)
    assert len(result.requirements) == 1


def test_empty_requirements_array_is_valid():
    result = RequirementSetOut.model_validate({"requirements": []})
    assert result.requirements == []


def test_wrong_key_name_raises():
    """A model that outputs '規範列' instead of 'requirements' must fail."""
    with pytest.raises(ValidationError):
        RequirementSetOut.model_validate({"規範列": [{"scenario": "test"}]})
