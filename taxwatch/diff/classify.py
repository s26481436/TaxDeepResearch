"""Classify change severity based on diff characteristics."""

from __future__ import annotations

import re

from taxwatch.diff.engine import ProvisionDiff
from taxwatch.models import Severity


def classify_severity(diff: ProvisionDiff) -> Severity:
    if diff.change_type == "added":
        return Severity.CRITICAL

    if diff.change_type == "removed":
        return Severity.CRITICAL

    if diff.change_type == "renumbered" and diff.similarity > 0.98:
        return Severity.COSMETIC

    if diff.change_type == "modified":
        if _is_cosmetic_change(diff.old_text, diff.new_text):
            return Severity.COSMETIC

        substantive_ratio = _substantive_change_ratio(diff.old_text, diff.new_text)
        if substantive_ratio > 0.3:
            return Severity.CRITICAL
        if substantive_ratio > 0.1:
            return Severity.MAJOR
        return Severity.MINOR

    return Severity.MINOR


def _is_cosmetic_change(old: str, new: str) -> bool:
    """Detect changes that are purely formatting/punctuation."""
    old_stripped = re.sub(r"[\s　,，.。;；:：、]+", "", old)
    new_stripped = re.sub(r"[\s　,，.。;；:：、]+", "", new)
    return old_stripped == new_stripped


def _substantive_change_ratio(old: str, new: str) -> float:
    """Estimate how much of the text actually changed (ignoring whitespace)."""
    old_chars = set(enumerate(old.replace(" ", "")))
    new_chars = set(enumerate(new.replace(" ", "")))
    if not old_chars and not new_chars:
        return 0.0
    total = max(len(old_chars), len(new_chars))
    if total == 0:
        return 0.0
    changed = len(old_chars.symmetric_difference(new_chars))
    return changed / total
