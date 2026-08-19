"""Tests for Stage 1: Research output ingestion."""

from __future__ import annotations

from taxwatch.requirements.importer import _map_columns, import_workbook


def test_english_headers_full_mapping():
    """2. 英文表頭完整對映到 11 個 field_key 及特殊欄位."""
    header = [
        "Tax Type",
        "Sub-item",
        "Taxpayer",
        "Requirement",
        "Tax Event",
        "Statutory Rate",
        "Taxable Item",
        "Calculation Formula",
        "Tax Base",
        "Deduction",
        "Incentive",
        "Filing Deadline",
        "Payment Deadline",
        "Collection Management",
        "Policy Basis & Remarks",
        "Change Content",
    ]
    mapping = _map_columns(header)
    mapped_keys = {mapping[i] for i in mapping}
    # Special columns
    assert mapping[0] == "_tax"
    assert mapping[1] == "_scenario"
    assert mapping[2] == "_role"
    assert mapping[14] == "_policy_basis"
    assert mapping[15] == "_change_content"

    # 11 standard field keys
    standard_fields = {
        "applicability",
        "taxable_event",
        "rate",
        "taxable_items",
        "formula",
        "tax_base",
        "deductions",
        "incentives",
        "filing_deadline",
        "payment_deadline",
        "administration",
    }
    assert standard_fields.issubset(mapped_keys)


def test_unmapped_headers_tracked():
    """3. 未對映的表頭出現在 unmapped_headers."""
    header = ["Tax Type", "Tax Scenario", "Unknown Column 1", "Statutory Rate", "Custom Notes"]
    mapping = _map_columns(header)
    unmapped = [header[i] for i in range(len(header)) if i not in mapping]
    assert unmapped == ["Unknown Column 1", "Custom Notes"]


def test_chinese_headers_still_work():
    """4. 中文表頭仍然正常(不得破壞既有行為)."""
    header = [
        "稅種",
        "課稅情境",
        "角色",
        "適用條件",
        "課稅事件",
        "稅率",
        "應稅項目",
        "計算公式",
        "稅基",
        "扣除",
        "租稅優惠",
        "申報期限",
        "繳款期限",
        "徵收管理",
    ]
    mapping = _map_columns(header)
    mapped_keys = {mapping[i] for i in mapping}
    assert mapping[0] == "_tax"
    assert mapping[1] == "_scenario"
    assert mapping[2] == "_role"
    assert len(mapped_keys) == 14
