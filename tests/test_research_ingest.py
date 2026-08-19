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


def test_source_note_saved_and_appended(tmp_path, session):
    """7. source_note 寫入 notes 且不覆蓋既有內容."""
    from taxwatch.models import TaxRequirement

    csv_content = """Tax Type,Sub-item,Taxpayer,Statutory Rate
增值稅,一般貨物,一般納稅人,13%
"""
    csv_file = tmp_path / "req_note.csv"
    csv_file.write_text(csv_content, encoding="utf-8")

    # First import with note 1
    import_workbook(
        session, csv_file, country="CN", source_note="gpt-researcher run 1 2026-08-19"
    )
    req = (
        session.query(TaxRequirement)
        .filter_by(scenario="一般貨物", taxpayer_role="一般納稅人")
        .first()
    )
    assert req is not None
    assert req.notes == "gpt-researcher run 1 2026-08-19"

    # Second import with note 2 (should append, not overwrite)
    import_workbook(
        session, csv_file, country="CN", source_note="manual adjustment 2026-08-20"
    )
    session.refresh(req)
    assert "gpt-researcher run 1 2026-08-19" in req.notes
    assert "manual adjustment 2026-08-20" in req.notes
    assert req.notes == "gpt-researcher run 1 2026-08-19\nmanual adjustment 2026-08-20"


def test_policy_basis_chinese_citations(tmp_path, session):
    """5. Policy Basis 含中文條號 → 驗證 ProvisionNode 存在才寫入 citations，且 needs_review 維持 True、confidence 為 0.0."""
    from datetime import datetime

    from taxwatch.models import (
        DocType,
        Document,
        ProvisionNode,
        Snapshot,
        Source,
        TaxRequirement,
    )

    source = Source(key="tw-mof", country="TW", connector="tw_law", description="TW MOF")
    session.add(source)
    session.flush()

    doc = Document(
        source_id=source.id,
        external_id="tw-income-tax-act",
        doc_type=DocType.STATUTE,
        title="所得稅法",
    )
    session.add(doc)
    session.flush()

    snap = Snapshot(
        document_id=doc.id,
        content_hash="h_tw",
        issued_at=datetime(2025, 1, 1),
        fetched_at=datetime(2026, 8, 1),
    )
    session.add(snap)
    session.flush()

    p88 = ProvisionNode(
        snapshot_id=snap.id,
        node_key="所得稅法#88",
        heading="第88條",
        text="扣繳規定",
        text_hash="h88",
    )
    p92 = ProvisionNode(
        snapshot_id=snap.id,
        node_key="所得稅法#92",
        heading="第92條",
        text="申報期限",
        text_hash="h92",
    )
    # Note: 所得稅法#66-9 is NOT added to session -> should be treated as unresolved
    session.add_all([p88, p92])
    session.commit()

    md_content = """
| Tax Type | Sub-item | Taxpayer | Statutory Rate | Policy Basis |
| :--- | :--- | :--- | :--- | :--- |
| 所得稅 | 薪資所得扣繳 | 扣繳義務人 | 5% | 依所得稅法第88條、第92條辦理扣繳申報 |
| 所得稅 | 未分配盈餘加徵 | 營利事業 | 5% | 所得稅法第66條之9規定 |
"""
    md_file = tmp_path / "req_citations.md"
    md_file.write_text(md_content, encoding="utf-8")

    stats = import_workbook(session, md_file, country="TW")
    assert stats["imported"] == 2
    # req1 has 2 resolved citations (88, 92); req2 has 1 unresolved citation (66-9 not in DB)
    assert stats["citations_resolved"] == 2
    assert stats["citations_unresolved"] == 1

    req1 = (
        session.query(TaxRequirement)
        .filter_by(scenario="薪資所得扣繳", taxpayer_role="扣繳義務人")
        .first()
    )
    assert req1 is not None
    by_key1 = {f.field_key: f for f in req1.fields}
    rate_field1 = by_key1["rate"]
    assert rate_field1.needs_review is True
    assert rate_field1.confidence == 0.0
    assert rate_field1.review_reason == "由試算表匯入，已對應條文但內容未經人工確認"
    assert len(rate_field1.citations) == 2
    node_keys1 = {c["node_key"] for c in rate_field1.citations}
    assert node_keys1 == {"所得稅法#88", "所得稅法#92"}

    req2 = (
        session.query(TaxRequirement)
        .filter_by(scenario="未分配盈餘加徵", taxpayer_role="營利事業")
        .first()
    )
    assert req2 is not None
    by_key2 = {f.field_key: f for f in req2.fields}
    rate_field2 = by_key2["rate"]
    assert rate_field2.needs_review is True
    assert rate_field2.confidence == 0.0
    assert rate_field2.review_reason == "由試算表匯入，尚未對應條文，法規異動時無法自動追蹤"
    assert rate_field2.citations == []


def test_policy_basis_english_citations(tmp_path, session):
    """6. Policy Basis 含英文條號 → citations 為空, citations_unresolved 計數增加."""
    from taxwatch.models import TaxRequirement

    md_content = """
| Tax Type | Sub-item | Taxpayer | Statutory Rate | Policy Basis |
| :--- | :--- | :--- | :--- | :--- |
| Income Tax | Salary Withholding | Withholding Agent | 5% | Income Tax Act Art. 92 |
"""
    md_file = tmp_path / "req_eng_citations.md"
    md_file.write_text(md_content, encoding="utf-8")

    stats = import_workbook(session, md_file, country="TW")
    assert stats["imported"] == 1
    assert stats["citations_resolved"] == 0
    assert stats["citations_unresolved"] == 1

    req = (
        session.query(TaxRequirement)
        .filter_by(scenario="Salary Withholding", taxpayer_role="Withholding Agent")
        .first()
    )
    assert req is not None
    by_key = {f.field_key: f for f in req.fields}
    rate_field = by_key["rate"]
    # Unresolved citations -> citations is empty, needs_review is True, confidence 0.0
    assert rate_field.citations == []
    assert rate_field.needs_review is True
    assert rate_field.confidence == 0.0
    assert rate_field.review_reason == "由試算表匯入，尚未對應條文，法規異動時無法自動追蹤"


def test_cli_import_requirements(tmp_path, session, monkeypatch):
    """Verify CLI import-requirements options and output."""
    from typer.testing import CliRunner

    from taxwatch.cli import app

    runner = CliRunner()
    md_content = """
| Tax Type | Sub-item | Taxpayer | Statutory Rate | Unknown Column |
| :--- | :--- | :--- | :--- | :--- |
| 增值稅 | 諮詢服務 | 一般納稅人 | 6% | 測試備註 |
"""
    md_file = tmp_path / "cli_req.md"
    md_file.write_text(md_content, encoding="utf-8")

    monkeypatch.setattr("taxwatch.db.get_session", lambda: session)
    monkeypatch.setattr("taxwatch.db.init_db", lambda: None)
    monkeypatch.setattr(session, "close", lambda: None)

    result = runner.invoke(
        app,
        [
            "import-requirements",
            str(md_file),
            "--country",
            "CN",
            "--source-note",
            "gpt-researcher 2026-08-19",
        ],
    )
    assert result.exit_code == 0
    assert "匯入 1 列" in result.output
    assert "Unknown Column" in result.output
    assert "未對應的表頭" in result.output
