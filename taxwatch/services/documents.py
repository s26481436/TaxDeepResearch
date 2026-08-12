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

from sqlalchemy import func
from sqlalchemy.orm import Session

from taxwatch.corpus.store import make_classifier
from taxwatch.diff.engine import diff_provisions
from taxwatch.graph.hierarchy import derive_parent_key
from taxwatch.graph.resolver import normalize_entity_key
from taxwatch.models import Change, Document, ProvisionNode, Snapshot, Source
from taxwatch.normalize.base import ProvisionData


class DocumentNotFound(LookupError):
    """Raised when no document matches the requested identifier."""


class AmbiguousDocument(LookupError):
    """Raised when a partial title matches more than one document.

    Carries the candidates so the caller can show them rather than making
    someone guess which spelling the crawler stored.
    """

    def __init__(self, term: str, candidates: list[str]):
        super().__init__(term)
        self.term = term
        self.candidates = candidates


class ParentLawMissing(LookupError):
    """Raised when a query names a statute we hold only the 子法 for.

    《中华人民共和国增值税法实施条例》 contains 《中华人民共和国增值税法》 as a
    literal prefix, so a substring search for the 母法 matches the 子法 — and
    when the 母法 has not been ingested that child is the *only* match, which
    the plain fallback would return as though it were the thing asked for.
    Answering a question about a statute with its implementing regulation is
    wrong in a way nobody downstream can detect, so say so instead.
    """

    def __init__(self, term: str, children: list[str]):
        super().__init__(term)
        self.term = term
        self.children = children


class SnapshotNotFound(LookupError):
    """Raised when a document has no snapshot for the requested date."""


@dataclass
class VersionEntry:
    version: str
    snapshot_id: int
    dated_at: datetime
    fetched_at: datetime
    has_official_date: bool
    content_hash: str
    provision_count: int
    changes: list[dict[str, Any]]


def find_document(session: Session, external_id: str) -> Document:
    """Resolve a document from an external id, a full title, or part of one.

    The external ids the crawlers mint are opaque (`c5251620`, a 文號), so
    nobody types them from memory. Accepting a distinctive fragment of the
    title — 增值税法 for 中华人民共和国增值税法 — is what makes the CLI usable
    at all, and an ambiguous fragment reports the candidates rather than
    silently picking one.

    Two things a bare substring search gets wrong, and this does not:

    * A law's working name is the same law. 增值税法 and 中华人民共和国增值税法
      normalise to one graph key, so the fragment names the statute exactly
      rather than sitting ambiguously between it and its 实施条例.
    * A 子法 is never a stand-in for its 母法. If every match merely descends
      from what was asked for, the statute itself is missing — raise rather
      than hand back the regulation.
    """
    doc = session.query(Document).filter_by(external_id=external_id).first()
    if doc is not None:
        return doc

    doc = session.query(Document).filter(Document.title == external_id).first()
    if doc is not None:
        return doc

    term = external_id.strip()
    if term:
        matches = session.query(Document).filter(Document.title.contains(term)).all()

        wanted = normalize_entity_key(term)
        named = [d for d in matches if normalize_entity_key(d.title) == wanted]
        if len(named) == 1:
            return named[0]

        if matches and all(_descends_from(d.title, wanted) for d in matches):
            raise ParentLawMissing(term, sorted(d.title for d in matches))
        if len(matches) == 1:
            return matches[0]
        if matches:
            raise AmbiguousDocument(term, sorted(d.title for d in matches))

    raise DocumentNotFound(external_id)


# A 子法 chain is at most 母法 → 實施條例 → 施行細則 in practice; the bound just
# stops a pathological title from looping.
_MAX_HIERARCHY_DEPTH = 4


def _descends_from(title: str, parent_key: str) -> bool:
    """Is `title` an implementing regulation *under* `parent_key`?

    Title-derived only, which is the case that matters here: the child that
    shadows its parent in a substring search is precisely the one that spells
    the parent out in its own name.
    """
    key = normalize_entity_key(title)
    for _ in range(_MAX_HIERARCHY_DEPTH):
        derived = derive_parent_key(key)
        if derived is None:
            return False
        if derived == parent_key:
            return True
        key = derived
    return False


def suggest_documents(session: Session, term: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """Documents whose title or id looks like what someone was reaching for.

    Used to turn a bare "not found" into something actionable.
    """
    query = session.query(Document, Source).join(Source, Document.source_id == Source.id)
    term = term.strip()
    if term:
        # Any single character of a Chinese law name is a strong hint; falling
        # back to the whole term first keeps the obvious matches on top.
        query = query.filter(Document.title.contains(term) | Document.external_id.contains(term))
    rows = query.limit(limit).all()
    if not rows and term:
        rows = (
            session.query(Document, Source)
            .join(Source, Document.source_id == Source.id)
            .limit(limit)
            .all()
        )
    return [
        {
            "external_id": doc.external_id,
            "title": doc.title,
            "country": source.country,
            "source_key": source.key,
        }
        for doc, source in rows
    ]


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

    classify_doc = make_classifier(session)
    rows: list[dict[str, Any]] = []
    seen_titles: set[str] = set()
    for doc, source in query.limit(limit).all():
        if doc.title in seen_titles:
            continue
        seen_titles.add(doc.title)
        tax_type = classify_doc(doc.title, doc.external_id)
        if tax_key and tax_type.key != tax_key:
            continue
        snapshots = _snapshots(session, doc.id)
        latest = snapshots[-1] if snapshots else None
        rows.append(
            {
                "external_id": doc.external_id,
                "title": doc.title,
                "url": doc.url,
                "doc_type": doc.doc_type.value,
                "country": source.country,
                "source_key": source.key,
                "tax_key": tax_type.key,
                "tax_name": tax_type.name_zh,
                "version_count": len(snapshots),
                "issued_at": doc.issued_at.isoformat() if doc.issued_at else None,
                "last_updated": latest.dated_at.isoformat() if latest else None,
                "official_date": bool(latest and latest.has_official_date),
                "last_crawled": latest.fetched_at.isoformat() if latest else None,
            }
        )

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
            dated_at=snapshot.dated_at,
            fetched_at=snapshot.fetched_at,
            has_official_date=snapshot.has_official_date,
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

    tax_type = make_classifier(session)(doc.title, doc.external_id)
    return {
        "external_id": doc.external_id,
        "title": doc.title,
        "url": doc.url,
        "tax_key": tax_type.key,
        "tax_name": tax_type.name_zh,
        "issued_at": doc.issued_at.isoformat() if doc.issued_at else None,
        "first_seen": snapshots[0].dated_at.isoformat(),
        "last_updated": snapshots[-1].dated_at.isoformat(),
        "first_crawled": snapshots[0].fetched_at.isoformat(),
        "last_crawled": snapshots[-1].fetched_at.isoformat(),
        "version_count": len(snapshots),
        "timeline": [
            {
                "version": e.version,
                "snapshot_id": e.snapshot_id,
                # The authority's own date where there is one; the crawl time
                # only as a last resort, and `official_date` says which.
                "date": e.dated_at.isoformat(),
                "official_date": e.has_official_date,
                "crawled_at": e.fetched_at.isoformat(),
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
        "snapshot_date": snapshot.dated_at.isoformat(),
        "crawled_at": snapshot.fetched_at.isoformat(),
        "snapshot_id": snapshot.id,
        "provision_count": len(provisions),
        "provisions": [
            {"node_key": p.node_key, "heading": p.heading, "text": p.text} for p in provisions
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
        "from_date": from_snapshot.dated_at.isoformat(),
        "to_date": to_snapshot.dated_at.isoformat(),
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


# Order the history the way a lawyer reads it — by the date on the document —
# and only fall back to crawl time for sources that publish no date.
_DATED_AT = func.coalesce(Snapshot.issued_at, Snapshot.fetched_at)


def _snapshots(session: Session, document_id: int) -> list[Snapshot]:
    return (
        session.query(Snapshot)
        .filter_by(document_id=document_id)
        .order_by(_DATED_AT.asc(), Snapshot.id.asc())
        .all()
    )


def _snapshot_at(session: Session, document_id: int, at: date | datetime) -> Snapshot:
    """The version in force on a date — by issue date, not by when we saw it."""
    moment = _as_datetime(at)
    snapshot = (
        session.query(Snapshot)
        .filter(Snapshot.document_id == document_id, _DATED_AT <= moment)
        .order_by(_DATED_AT.desc(), Snapshot.id.desc())
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
