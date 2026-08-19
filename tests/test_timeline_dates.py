"""A change belongs on the timeline at the date the authority published it.

`detected_at` is when the crawler happened to look. A first crawl stamps today
on decades of law at once, which collapses the whole corpus onto one date and
puts a 2016 announcement above a 2025 amendment purely by crawl order.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

import pytest

from taxwatch.models import (
    Change,
    ChangeType,
    DocType,
    Document,
    Severity,
    Snapshot,
    Source,
)
from taxwatch.services import dashboard as dash
from taxwatch.services import tax_types as tt


def _change(session, doc, *, issued, detected, node_key):
    snapshot = Snapshot(
        document_id=doc.id,
        content_hash=hashlib.sha256(node_key.encode()).hexdigest(),
        issued_at=issued,
        fetched_at=detected,
    )
    session.add(snapshot)
    session.flush()
    change = Change(
        document_id=doc.id,
        to_snapshot_id=snapshot.id,
        node_key=node_key,
        change_type=ChangeType.MODIFIED,
        severity=Severity.MAJOR,
        detected_at=detected,
    )
    session.add(change)
    session.flush()
    return change


@pytest.fixture
def two_changes(session):
    """Crawled in one pass; published nine years apart."""
    source = Source(key="cn-chinatax", country="CN", connector="cn_chinatax")
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

    crawl = datetime(2026, 8, 19, 10, 0)
    old = _change(session, doc, issued=datetime(2016, 3, 1), detected=crawl, node_key="增值税法#1")
    new = _change(
        session, doc, issued=datetime(2025, 12, 1), detected=crawl, node_key="增值税法#2"
    )
    session.commit()
    return {"old": old, "new": new}


def test_changes_carry_the_publication_date(session, two_changes):
    rows = dash.list_changes(session, days=3650)
    by_node = {r["node_key"]: r for r in rows}
    assert by_node["增值税法#1"]["issued_at"].startswith("2016-03-01")
    assert by_node["增值税法#2"]["issued_at"].startswith("2025-12-01")


def test_newest_publication_comes_first(session, two_changes):
    """Both were detected in the same crawl, so detection order says nothing."""
    rows = dash.list_changes(session, days=3650)
    assert [r["node_key"] for r in rows][:2] == ["增值税法#2", "增值税法#1"]


def test_detection_time_is_still_available(session, two_changes):
    rows = dash.list_changes(session, days=3650)
    assert all(r["detected_at"].startswith("2026-08-19") for r in rows)


def test_official_date_flag_marks_an_inferred_date(session):
    """A source publishing no date must not look as though it published one."""
    source = Source(key="x", country="CN", connector="cn_chinatax")
    session.add(source)
    session.flush()
    doc = Document(source_id=source.id, external_id="d", doc_type=DocType.ANNOUNCEMENT, title="公告")
    session.add(doc)
    session.flush()
    _change(session, doc, issued=None, detected=datetime(2026, 8, 19), node_key="公告#1")
    session.commit()

    row = dash.list_changes(session, days=3650)[0]
    assert row["official_date"] is False
    assert row["issued_at"].startswith("2026-08-19")  # falls back to fetch time


def test_tax_type_summary_orders_by_publication(session, two_changes):
    summary = tt.get_summary(session, "cn_vat", recent_days=3650)
    nodes = [c["node_key"] for c in summary["changes"]]
    assert nodes[:2] == ["增值税法#2", "增值税法#1"]
    assert summary["changes"][0]["official_date"] is True
