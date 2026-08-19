"""A long extraction must not die on a connection that went stale while it waited.

Batched extraction spends minutes to tens of minutes in LLM calls once gateway
retries and suspension backoffs are counted. A pooled connection held open
across that is routinely closed by the server or a connection proxy, and the
failure surfaces on the first ordinary SELECT afterwards:

    OperationalError: server closed the connection unexpectedly
"""

from __future__ import annotations

import hashlib
from datetime import datetime

import pytest

from taxwatch.models import DocType, Document, ProvisionNode, Snapshot, Source


@pytest.fixture
def vat_law_for_liveness(session):
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
    snapshot = Snapshot(document_id=doc.id, content_hash="v1", issued_at=datetime(2024, 12, 25))
    session.add(snapshot)
    session.flush()
    text = "销售货物，税率为百分之十三。"
    session.add(
        ProvisionNode(
            snapshot_id=snapshot.id,
            node_key="增值税法#2",
            heading="第2条",
            text=text,
            text_hash=hashlib.sha256(text.encode()).hexdigest(),
        )
    )
    session.commit()
    return doc

from unittest.mock import MagicMock, patch

import taxwatch.db as db


def test_engine_validates_connections_on_checkout(monkeypatch):
    monkeypatch.setattr(db, "_engine", None)
    captured = {}

    def fake_create_engine(url, **kwargs):
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(db, "create_engine", fake_create_engine)
    db.get_engine()
    monkeypatch.setattr(db, "_engine", None)

    assert captured["pool_pre_ping"] is True, "a dead pooled connection must be replaced"
    assert captured["pool_recycle"] > 0, "connections must not be kept forever"


def test_read_transaction_is_closed_before_the_llm_phase(session, vat_law_for_liveness):
    """The connection must be back in the pool while the LLM is being called."""
    from taxwatch.requirements.extract import extract_for_document
    from taxwatch.requirements.schema import RequirementSetOut

    in_transaction_during_llm = {}

    client = MagicMock(model="m")

    def generate(*args, **kwargs):
        in_transaction_during_llm["value"] = session.in_transaction()
        return RequirementSetOut(requirements=[], unresolved=[])

    client.generate_structured.side_effect = generate

    with patch("taxwatch.requirements.extract.get_llm_client", return_value=client):
        extract_for_document(session, "cn-vat-law")

    assert in_transaction_during_llm["value"] is False
