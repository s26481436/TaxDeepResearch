"""Document version-history queries.

A Document accumulates one Snapshot per fetch that changed its content hash,
so the snapshot list *is* the version history. Diffs between arbitrary
versions are computed on demand from the stored provisions rather than read
from the `changes` table, which only records consecutive-snapshot deltas.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from taxwatch.diff.engine import diff_provisions
from taxwatch.models import Change, Document, ProvisionNode, Snapshot, Source
from taxwatch.normalize.base import ProvisionData
from taxwatch.taxonomy import classify


class DocumentNotFound(LookupError):
    """Raised when no document matches the requested identifier."""


class SnapshotNotFound(LookupError):
    """Raised when a document has no snapshot for the requested date."""


@dataclass
class VersionEntry:
    version: str
    snapshot_id: int
    fetched_at: datetime
    content_hash: str
    provision_count: int
    changes: list[dict[str, Any]]


def find_document(session: Session, external_id: str) -> Document:
    doc = session.query(Document).filter_by(external_id=external_id).first()
    if doc is None:
        doc = session.query(Document).filter(Document.title == external_id).first()
    if doc is None:
        raise DocumentNotFound(external_id)
    return doc


def list_documents(
    session: Session,
    *,
    country: str | None = None,
    tax_key: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """List documents with their latest-snapshot metadata."""
    query = session.query(Document, Source).join(Source, Document.source_id == Source.id)
    if country:
        query = query.filter(Source.country == country)

    rows: list[dict[str, Any]] = []
    for doc, source in query.limit(limit).all():
        tax_type = classify(doc.title)
        if tax_key and tax_type.key != tax_key:
            continue
        snapshots = _snapshots(session, doc.id)
        latest = snapshots[-1] if snapshots else None
        rows.append({
            "external_id": doc.external_id,
            "title": doc.title,
            "url": doc.url,
            "doc_type": doc.doc_type.value,
            "country": source.country,
            "source_key": source.key,
            "tax_key": tax_type.key,
            "tax_name": tax_type.name_zh,
            "version_count": len(snapshots),
            "last_updated": latest.fetched_at.isoformat() if latest else None,
        })

    rows.sort(key=lambda r: r["last_updated"] or "", reverse=True)
    return rows


def get_history(session: Session, external_id: str) -> dict[str, Any]:
    """Full version timeline for one document, newest last."""
    doc = find_document(session, external_id)
    snapshots = _snapshots(session, doc.id)
    if not snapshots:
        raise SnapshotNotFound(external_id)

    changes_by_snapshot: dict[int, list[Change]] = {}
    for change in session.query(Change).filter_by(document_id=doc.id).all():
        changes_by_snapshot.setdefault(change.to_snapshot_id, []).append(change)

    timeline: list[VersionEntry] = []
    for index, snapshot in enumerate(snapshots, start=1):
        entry = VersionEntry(
            version=f"v{index}",
            snapshot_id=snapshot.id,
            fetched_at=snapshot.fetched_at,
            content_hash=snapshot.content_hash,
            provision_count=_provision_count(session, snapshot.id),
            changes=[
                {
                    "id": c.id,
                    "node_key": c.node_key,
                    "change_type": c.change_type.value,
                    "severity": c.severity.value,
                }
                for c in changes_by_snapshot.get(snapshot.id, [])
            ],
        )
        timeline.append(entry)

    tax_type = classify(doc.title)
    return {
        "external_id": doc.external_id,
        "title": doc.title,
        "url": doc.url,
        "tax_key": tax_type.key,
        "tax_name": tax_type.name_zh,
        "first_seen": snapshots[0].fetched_at.isoformat(),
        "last_updated": snapshots[-1].fetched_at.isoformat(),
        "version_count": len(snapshots),
        "timeline": [
            {
                "version": e.version,
                "snapshot_id": e.snapshot_id,
                "date": e.fetched_at.isoformat(),
                "content_hash": e.content_hash,
                "provision_count": e.provision_count,
                "changes": e.changes,
            }
            for e in timeline
        ],
    }


def get_version_at(session: Session, external_id: str, at: date | datetime) -> dict[str, Any]:
    """The document as it stood on a given date — the newest snapshot at or before it."""
    doc = find_document(session, external_id)
    snapshot = _snapshot_at(session, doc.id, at)
    provisions = _provisions(session, snapshot.id)

    return {
        "external_id": doc.external_id,
        "title": doc.title,
        "requested_date": _as_datetime(at).isoformat(),
        "snapshot_date": snapshot.fetched_at.isoformat(),
        "snapshot_id": snapshot.id,
        "provision_count": len(provisions),
        "provisions": [
            {"node_key": p.node_key, "heading": p.heading, "text": p.text}
            for p in provisions
        ],
    }


def get_diff(
    session: Session,
    external_id: str,
    from_at: date | datetime,
    to_at: date | datetime,
) -> dict[str, Any]:
    """Provision-level diff between the versions in force on two dates."""
    doc = find_document(session, external_id)
    from_snapshot = _snapshot_at(session, doc.id, from_at)
    to_snapshot = _snapshot_at(session, doc.id, to_at)

    old = [_to_provision_data(p) for p in _provisions(session, from_snapshot.id)]
    new = [_to_provision_data(p) for p in _provisions(session, to_snapshot.id)]
    diffs = diff_provisions(old, new)

    counts: dict[str, int] = {}
    for d in diffs:
        counts[d.change_type] = counts.get(d.change_type, 0) + 1

    return {
        "external_id": doc.external_id,
        "title": doc.title,
        "from_date": from_snapshot.fetched_at.isoformat(),
        "to_date": to_snapshot.fetched_at.isoformat(),
        "from_snapshot_id": from_snapshot.id,
        "to_snapshot_id": to_snapshot.id,
        "unchanged": from_snapshot.id == to_snapshot.id,
        "summary": {
            "added": counts.get("added", 0),
            "removed": counts.get("removed", 0),
            "modified": counts.get("modified", 0),
            "renumbered": counts.get("renumbered", 0),
            "total": len(diffs),
        },
        "diffs": [
            {
                "node_key": d.node_key,
                "change_type": d.change_type,
                "old_heading": d.old_heading,
                "new_heading": d.new_heading,
                "old_text": d.old_text,
                "new_text": d.new_text,
                "diff_text": d.diff_text,
                "similarity": round(d.similarity, 3),
            }
            for d in diffs
        ],
    }


# ---------- internals ----------

def _snapshots(session: Session, document_id: int) -> list[Snapshot]:
    return (
        session.query(Snapshot)
        .filter_by(document_id=document_id)
        .order_by(Snapshot.fetched_at.asc(), Snapshot.id.asc())
        .all()
    )


def _snapshot_at(session: Session, document_id: int, at: date | datetime) -> Snapshot:
    moment = _as_datetime(at)
    snapshot = (
        session.query(Snapshot)
        .filter(Snapshot.document_id == document_id, Snapshot.fetched_at <= moment)
        .order_by(Snapshot.fetched_at.desc(), Snapshot.id.desc())
        .first()
    )
    if snapshot is None:
        raise SnapshotNotFound(f"no snapshot on or before {moment.isoformat()}")
    return snapshot


def _provisions(session: Session, snapshot_id: int) -> list[ProvisionNode]:
    return (
        session.query(ProvisionNode)
        .filter_by(snapshot_id=snapshot_id)
        .order_by(ProvisionNode.id.asc())
        .all()
    )


def _provision_count(session: Session, snapshot_id: int) -> int:
    return session.query(ProvisionNode).filter_by(snapshot_id=snapshot_id).count()


def _to_provision_data(node: ProvisionNode) -> ProvisionData:
    return ProvisionData(node_key=node.node_key, heading=node.heading, text=node.text)


def _as_datetime(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.combine(value, datetime.max.time())
