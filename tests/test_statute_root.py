"""A statute is the root of its family; it has no 母法 to walk up to.

所得稅法 contains 「應依所得來源國稅法規定繳納之所得稅」. The citation regex reads
所得來源國稅法 as a law — it is 所得來源國 followed by 稅法 — and filed the
statute beneath it. Extraction then refused to run at all, reporting that the
母法《所得來源國稅法》 had not been fetched. It never can be.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

import pytest

from taxwatch.graph.citation import extract_citations
from taxwatch.graph.hierarchy import promote_declared_authority
from taxwatch.models import DocType, Document, ProvisionNode, Snapshot, Source
from taxwatch.services.consolidated import get_consolidated


def _doc(session, *, doc_type, title, external_id, node_key, text):
    source = session.query(Source).filter_by(key="tw-moj-law").first()
    if source is None:
        source = Source(key="tw-moj-law", country="TW", connector="tw_moj_law")
        session.add(source)
        session.flush()
    doc = Document(
        source_id=source.id, external_id=external_id, doc_type=doc_type, title=title
    )
    session.add(doc)
    session.flush()
    snap = Snapshot(document_id=doc.id, content_hash=external_id, issued_at=datetime(2025, 1, 1))
    session.add(snap)
    session.flush()
    session.add(
        ProvisionNode(
            snapshot_id=snap.id,
            node_key=node_key,
            heading="第3條",
            text=text,
            text_hash=hashlib.sha256(text.encode()).hexdigest(),
        )
    )
    session.commit()
    return doc


FOREIGN_CREDIT = "其來自中華民國境外之營利事業所得，應依所得來源國稅法規定繳納之所得稅。"


def test_the_phrase_really_does_look_like_a_law():
    """Guarding the walk is the fix; the regex cannot tell these apart."""
    keys = [c.entity_key for c in extract_citations(FOREIGN_CREDIT)]
    assert "所得來源國稅法" in keys


def test_a_statute_is_its_own_root(session):
    doc = _doc(
        session,
        doc_type=DocType.STATUTE,
        title="所得稅法",
        external_id="tw-income-tax-act",
        node_key="所得稅法#3",
        text=FOREIGN_CREDIT,
    )
    view = get_consolidated(session, doc.external_id)

    assert view["title"] == "所得稅法"
    assert view.get("missing_parent") is None


def test_a_regulation_still_walks_up(session):
    """The guard must not flatten genuine 子法 relationships."""
    statute = _doc(
        session,
        doc_type=DocType.STATUTE,
        title="增值税法",
        external_id="cn-vat",
        node_key="增值税法#1",
        text="在中华人民共和国境内销售货物的单位和个人，为增值税的纳税人。",
    )
    child = _doc(
        session,
        doc_type=DocType.REGULATION,
        title="增值税法实施条例",
        external_id="cn-vat-reg",
        node_key="增值税法实施条例#1",
        text="本条例依增值税法第二十七条制定。",
    )
    promote_declared_authority(
        session, child, "增值税法实施条例", list(_provisions(session, child))
    )
    session.commit()

    view = get_consolidated(session, child.external_id)
    assert view["title"] == statute.title


def _provisions(session, doc):
    snap = session.query(Snapshot).filter_by(document_id=doc.id).first()
    return session.query(ProvisionNode).filter_by(snapshot_id=snap.id).all()


def test_promote_declared_authority_skips_statutes(session):
    doc = _doc(
        session,
        doc_type=DocType.STATUTE,
        title="所得稅法",
        external_id="tw-income-tax-act-2",
        node_key="所得稅法#3",
        text=FOREIGN_CREDIT,
    )
    relations = promote_declared_authority(
        session, doc, "所得稅法", list(_provisions(session, doc))
    )
    assert relations == []
