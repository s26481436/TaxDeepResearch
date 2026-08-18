"""Tests for affected_by_node_keys and impacted requirements view (Stage 0)."""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from taxwatch.api.routes import dashboard as dashboard_route
from taxwatch.models import (
    Change,
    ChangeType,
    DocType,
    Document,
    FieldSource,
    RequirementField,
    RequirementStatus,
    Severity,
    Snapshot,
    Source,
    TaxRequirement,
)
from taxwatch.services.dashboard import get_change_detail, list_changes
from taxwatch.services.requirements import affected_by_node_keys
from taxwatch.web import app as web_app


@pytest.fixture
def client(session, monkeypatch):
    monkeypatch.setattr(session, "close", lambda: None)
    monkeypatch.setattr(dashboard_route, "get_session", lambda: session)
    monkeypatch.setattr(web_app, "get_session", lambda: session)
    return TestClient(web_app.app)


@pytest.fixture
def sample_data(session):
    source = Source(
        key="cn-chinatax",
        country="CN",
        connector="gov_cn",
        description="China Tax",
    )
    session.add(source)
    session.flush()

    doc = Document(
        source_id=source.id,
        external_id="cn-vat-law",
        doc_type=DocType.STATUTE,
        title="中华人民共和国增值税法",
    )
    session.add(doc)
    session.flush()

    snap = Snapshot(
        document_id=doc.id,
        content_hash="h1",
        issued_at=datetime(2025, 1, 1),
        fetched_at=datetime(2026, 8, 1),
    )
    session.add(snap)
    session.flush()

    # Create tax requirements
    r1 = TaxRequirement(
        country="CN",
        tax_key="cn_vat",
        scenario="小規模納稅人銷售貨物",
        taxpayer_role="小規模納稅人 - 簡易計稅",
        status=RequirementStatus.REVIEWED,
    )
    r2 = TaxRequirement(
        country="CN",
        tax_key="cn_vat",
        scenario="一般納稅人提供勞務",
        taxpayer_role="一般納稅人 - 一般計稅",
        status=RequirementStatus.DRAFT,
    )
    session.add_all([r1, r2])
    session.flush()

    # r1 field 1: citing 中华人民共和国增值税法#12 (already reviewed: needs_review=False)
    f1_1 = RequirementField(
        requirement_id=r1.id,
        field_key="rate",
        value="3%",
        citations=[{"node_key": "中华人民共和国增值税法#12", "title": "增值税法"}],
        needs_review=False,
        source=FieldSource.LLM,
    )
    # r1 field 2: citing 增值税法#12
    f1_2 = RequirementField(
        requirement_id=r1.id,
        field_key="tax_basis",
        value="不含稅銷售額",
        citations=[{"node_key": "增值税法#12", "title": "增值税法"}],
        needs_review=True,
        source=FieldSource.LLM,
    )
    # r2 field: citing 增值税法#9 (with whitespace and traditional '台')
    f2_1 = RequirementField(
        requirement_id=r2.id,
        field_key="rate",
        value="13%",
        citations=[{"node_key": " 增值税法 #9 ", "title": "增值税法"}],
        needs_review=False,
        source=FieldSource.LLM,
    )
    session.add_all([f1_1, f1_2, f2_1])

    # Create Changes
    c1 = Change(
        document_id=doc.id,
        to_snapshot_id=snap.id,
        node_key="增值税法#12",
        change_type=ChangeType.MODIFIED,
        severity=Severity.MAJOR,
        detected_at=datetime.utcnow(),
    )
    c2 = Change(
        document_id=doc.id,
        to_snapshot_id=snap.id,
        node_key="增值税法#99",
        change_type=ChangeType.ADDED,
        severity=Severity.MINOR,
        detected_at=datetime.utcnow(),
    )
    session.add_all([c1, c2])
    session.commit()
    return {"doc": doc, "r1": r1, "r2": r2, "c1": c1, "c2": c2}


def test_affected_by_node_keys_multiple_fields_hit(session, sample_data):
    """1. 一個 change 的 node_key 被兩格引用 → 兩格都出現在結果中."""
    res = affected_by_node_keys(session, ["增值税法#12"])
    items = res.get("增值税法#12", [])
    assert len(items) == 2
    field_keys = {item["field_key"] for item in items}
    assert field_keys == {"rate", "tax_basis"}
    assert all(item["taxpayer_role"] == "小規模納稅人 - 簡易計稅" for item in items)


def test_affected_by_node_keys_normalization(session, sample_data):
    """2. 簡繁或前綴不同但正規化後相同的 node_key → 仍然配對成功."""
    # Searching with prefix 中华人民共和国 should match fields citing 增值税法#12 or 中华人民共和国增值税法#12
    res = affected_by_node_keys(session, ["中华人民共和国增值税法#12"])
    norm_key = "增值税法#12"
    assert norm_key in res
    assert len(res[norm_key]) == 2

    # Searching with brackets 《增值税法》#9 should match 《增值税法》#9
    res9 = affected_by_node_keys(session, ["增值税法#9"])
    assert len(res9.get("增值税法#9", [])) == 1
    assert res9["增值税法#9"][0]["field_key"] == "rate"
    assert res9["增值税法#9"][0]["value"] == "13%"


def test_affected_by_node_keys_includes_reviewed(session, sample_data):
    """3. 已覆核(needs_review=False)的格子 → 仍然出現在結果中."""
    res = affected_by_node_keys(session, ["增值税法#12"])
    items = res.get("增值税法#12", [])
    rate_item = next(i for i in items if i["field_key"] == "rate")
    assert rate_item["needs_review"] is False
    assert rate_item["value"] == "3%"


def test_affected_by_node_keys_empty_or_miss(session, sample_data):
    """4. 沒有任何格子引用該 node_key → 回傳空清單,不拋例外."""
    res = affected_by_node_keys(session, ["增值税法#99", "不存在法規#1"])
    assert res.get("增值税法#99") == []
    assert res.get("不存在法規#1") == []

    # Empty iterable
    res_empty = affected_by_node_keys(session, [])
    assert res_empty == {}


def test_single_query_across_multiple_changes(session, sample_data):
    """5. 多個 change → 只查詢一次資料庫(驗證單次查詢建索引)."""
    original_query = session.query
    query_calls = []

    def spy_query(*args, **kwargs):
        query_calls.append(args)
        return original_query(*args, **kwargs)

    session.query = spy_query
    try:
        affected_by_node_keys(session, ["增值税法#12", "增值税法#9", "增值税法#99"])
        # Should only execute one query for RequirementField
        assert len(query_calls) == 1
    finally:
        session.query = original_query


def test_list_changes_and_detail_affected_requirements(session, sample_data):
    """Verify list_changes and get_change_detail include affected_requirements."""
    changes = list_changes(session, days=30)
    assert len(changes) >= 2
    c12 = next(c for c in changes if c["node_key"] == "增值税法#12")
    c99 = next(c for c in changes if c["node_key"] == "增值税法#99")

    assert "affected_requirements" in c12
    assert len(c12["affected_requirements"]) == 2
    assert "affected_requirements" in c99
    assert c99["affected_requirements"] == []

    # get_change_detail
    detail12 = get_change_detail(session, sample_data["c1"].id)
    assert "affected_requirements" in detail12
    assert len(detail12["affected_requirements"]) == 2

    detail99 = get_change_detail(session, sample_data["c2"].id)
    assert "affected_requirements" in detail99
    assert detail99["affected_requirements"] == []


def test_api_changes_endpoints_include_affected_requirements(client, sample_data):
    """Verify API /api/changes and /api/changes/{id} return affected_requirements."""
    # List endpoint
    res = client.get("/api/changes?days=30")
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 2
    c12 = next(c for c in data if c["node_key"] == "增值税法#12")
    assert "affected_requirements" in c12
    assert len(c12["affected_requirements"]) == 2
    assert c12["affected_requirements"][0]["taxpayer_role"] == "小規模納稅人 - 簡易計稅"

    # Detail endpoint
    c1_id = sample_data["c1"].id
    res_detail = client.get(f"/api/changes/{c1_id}")
    assert res_detail.status_code == 200
    detail_data = res_detail.json()
    assert "affected_requirements" in detail_data
    assert len(detail_data["affected_requirements"]) == 2
