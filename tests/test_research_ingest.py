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


def test_markdown_table_parsing(tmp_path, session):
    """1. Markdown 表格解析: 表頭、--- 分隔列略過、<br> 轉換、** 去除."""
    md_content = """
# 稅務規範表

| Tax Type | Sub-item | Taxpayer | Statutory Rate | Collection Management |
| :--- | :--- | :--- | :--- | :--- |
| **增值稅** | 一般貨物銷售 | **一般納稅人** | 13% | 按月申報<br>次月15日內繳納 |
| 增值稅 | 小規模簡易計稅 | 小規模納稅人 | 3% | 按季申報 |
| | | | | |

"""
    md_file = tmp_path / "requirements.md"
    md_file.write_text(md_content, encoding="utf-8")

    stats = import_workbook(session, md_file, country="CN")
    assert stats["rows"] == 2
    assert stats["imported"] == 2
    assert stats["skipped"] == 0

    from taxwatch.models import TaxRequirement

    req1 = (
        session.query(TaxRequirement)
        .filter_by(scenario="一般貨物銷售", taxpayer_role="一般納稅人")
        .first()
    )
    assert req1 is not None
    by_key = {f.field_key: f for f in req1.fields}
    assert by_key["rate"].value == "13%"
    # Verify <br> transformed to newline
    assert by_key["administration"].value == "按月申報\n次月15日內繳納"


def test_csv_table_parsing(tmp_path, session):
    """8. CSV 讀取."""
    csv_content = """Tax Type,Sub-item,Taxpayer,Statutory Rate,Filing Deadline
增值稅,諮詢服務,一般納稅人,6%,次月15日
"""
    csv_file = tmp_path / "requirements.csv"
    csv_file.write_text(csv_content, encoding="utf-8")

    stats = import_workbook(session, csv_file, country="CN")
    assert stats["rows"] == 1
    assert stats["imported"] == 1

    from taxwatch.models import TaxRequirement

    req = (
        session.query(TaxRequirement)
        .filter_by(scenario="諮詢服務", taxpayer_role="一般納稅人")
        .first()
    )
    assert req is not None
    by_key = {f.field_key: f for f in req.fields}
    assert by_key["rate"].value == "6%"
    assert by_key["filing_deadline"].value == "次月15日"
