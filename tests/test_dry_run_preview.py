"""A dry run must show what it would produce, not just how much.

The tax-keyed path aggregated counts from each document but dropped `preview`,
so `--dry-run` reported "抽出 160 個課稅情境" and listed none of them — leaving
no way to judge whether the scenarios were sensible before writing them.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from taxwatch.models import DocType, Document, ProvisionNode, Snapshot, Source
from taxwatch.requirements.extract import extract_for_tax
from taxwatch.requirements.schema import RequirementOut, RequirementSetOut


@pytest.fixture
def vat_law(session):
    source = Source(key="cn-chinatax", country="CN", connector="cn_chinatax")
    session.add(source)
    session.flush()
    doc = Document(
        source_id=source.id,
        external_id="cn-vat-law",
        doc_type=DocType.STATUTE,
        title="中华人民共和国增值税法",
        issued_at=datetime(2024, 12, 25),
    )
    session.add(doc)
    session.flush()
    snap = Snapshot(document_id=doc.id, content_hash="v1", issued_at=datetime(2024, 12, 25))
    session.add(snap)
    session.flush()
    text = "销售货物，税率为百分之十三。"
    session.add(
        ProvisionNode(
            snapshot_id=snap.id,
            node_key="增值税法#2",
            heading="第2条",
            text=text,
            text_hash=hashlib.sha256(text.encode()).hexdigest(),
        )
    )
    session.commit()
    return doc


def _client(scenarios):
    client = MagicMock(model="test-model")
    client.generate_structured.return_value = RequirementSetOut(
        requirements=[
            RequirementOut(scenario=s, taxpayer_role=r, fields=[]) for s, r in scenarios
        ],
        unresolved=["需人工補充：申報平台"],
    )
    return client


def test_tax_level_dry_run_lists_the_scenarios(session, vat_law):
    client = _client([("一般貨物銷售", "一般納稅人"), ("小規模銷售", "小規模納稅人")])
    with patch("taxwatch.requirements.extract.get_llm_client", return_value=client):
        stats = extract_for_tax(session, "cn_vat", dry_run=True)

    assert stats["requirements"] == 2
    names = [p["scenario"] for p in stats["preview"]]
    assert names == ["一般貨物銷售", "小規模銷售"]
    assert stats["preview"][0]["taxpayer_role"] == "一般納稅人"


def test_tax_level_dry_run_carries_unresolved(session, vat_law):
    client = _client([("一般貨物銷售", "一般納稅人")])
    with patch("taxwatch.requirements.extract.get_llm_client", return_value=client):
        stats = extract_for_tax(session, "cn_vat", dry_run=True)

    assert "需人工補充：申報平台" in stats["unresolved"]


def test_dry_run_writes_nothing(session, vat_law):
    from taxwatch.models import TaxRequirement

    client = _client([("一般貨物銷售", "一般納稅人")])
    with patch("taxwatch.requirements.extract.get_llm_client", return_value=client):
        extract_for_tax(session, "cn_vat", dry_run=True)

    assert session.query(TaxRequirement).count() == 0
