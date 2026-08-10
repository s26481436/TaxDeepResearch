"""Tax-type rollups — the "what is the current state of each 稅種" view."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from taxwatch.corpus.store import make_classifier
from taxwatch.models import Analysis, Change, Document, ProvisionNode, Snapshot, Source
from taxwatch.taxonomy import UNCLASSIFIED


class TaxTypeNotFound(LookupError):
    """Raised when no monitored document belongs to the requested tax type."""


def list_tax_types(session: Session, *, recent_days: int = 7) -> list[dict[str, Any]]:
    """One row per tax type with its freshness and recent-change counts."""
    cutoff = datetime.utcnow() - timedelta(days=recent_days)
    classify_doc = make_classifier(session)
    buckets: dict[str, dict[str, Any]] = {}

    for doc, source in _documents_with_sources(session):
        tax_type = classify_doc(doc.title, doc.external_id)
        bucket = buckets.setdefault(tax_type.key, {
            "key": tax_type.key,
            "name": tax_type.name_zh,
            "countries": set(),
            "document_count": 0,
            "version_count": 0,
            "recent_changes": 0,
            "critical_changes": 0,
            "last_updated": None,
        })

        bucket["countries"].add(source.country)
        bucket["document_count"] += 1

        snapshots = (
            session.query(Snapshot)
            .filter_by(document_id=doc.id)
            .order_by(Snapshot.fetched_at.desc())
            .all()
        )
        bucket["version_count"] += len(snapshots)
        if snapshots:
            latest = snapshots[0].fetched_at
            if bucket["last_updated"] is None or latest > bucket["last_updated"]:
                bucket["last_updated"] = latest

        for change in (
            session.query(Change)
            .filter(Change.document_id == doc.id, Change.detected_at >= cutoff)
            .all()
        ):
            bucket["recent_changes"] += 1
            if change.severity.value in ("critical", "major"):
                bucket["critical_changes"] += 1

    now = datetime.utcnow()
    rows: list[dict[str, Any]] = []
    for bucket in buckets.values():
        last = bucket.pop("last_updated")
        rows.append({
            **bucket,
            "countries": sorted(bucket["countries"]),
            "last_updated": last.isoformat() if last else None,
            "days_since_update": (now - last).days if last else None,
            "status": _status(bucket["recent_changes"], bucket["critical_changes"]),
        })

    rows.sort(key=lambda r: (-r["recent_changes"], r["name"]))
    return rows


def get_summary(
    session: Session,
    tax_key: str,
    *,
    recent_days: int = 90,
) -> dict[str, Any]:
    """Deep view of one tax type: its documents, versions and analysed changes."""
    cutoff = datetime.utcnow() - timedelta(days=recent_days)
    classify_doc = make_classifier(session)

    documents: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    countries: set[str] = set()
    tax_name = ""
    confidence_values: list[float] = []
    latest_overall: datetime | None = None

    for doc, source in _documents_with_sources(session):
        tax_type = classify_doc(doc.title, doc.external_id)
        if tax_type.key != tax_key:
            continue
        tax_name = tax_type.name_zh
        countries.add(source.country)

        snapshots = (
            session.query(Snapshot)
            .filter_by(document_id=doc.id)
            .order_by(Snapshot.fetched_at.desc())
            .all()
        )
        latest = snapshots[0] if snapshots else None
        if latest and (latest_overall is None or latest.fetched_at > latest_overall):
            latest_overall = latest.fetched_at

        documents.append({
            "external_id": doc.external_id,
            "title": doc.title,
            "url": doc.url,
            "doc_type": doc.doc_type.value,
            "country": source.country,
            "source_key": source.key,
            "version_count": len(snapshots),
            "provision_count": (
                session.query(ProvisionNode).filter_by(snapshot_id=latest.id).count()
                if latest else 0
            ),
            "first_seen": snapshots[-1].fetched_at.isoformat() if snapshots else None,
            "last_updated": latest.fetched_at.isoformat() if latest else None,
        })

        rows = (
            session.query(Change, Analysis)
            .outerjoin(Analysis, Analysis.change_id == Change.id)
            .filter(Change.document_id == doc.id, Change.detected_at >= cutoff)
            .order_by(Change.detected_at.desc())
            .all()
        )
        for change, analysis in rows:
            if analysis is not None:
                confidence_values.append(analysis.confidence)
            changes.append({
                "id": change.id,
                "document_title": doc.title,
                "external_id": doc.external_id,
                "node_key": change.node_key,
                "change_type": change.change_type.value,
                "severity": change.severity.value,
                "detected_at": change.detected_at.isoformat(),
                "summary": analysis.summary_zh if analysis else "",
                "effective_date": analysis.effective_date if analysis else "",
                "affected_parties": analysis.affected_parties if analysis else [],
                "confidence": analysis.confidence if analysis else None,
            })

    if not documents:
        raise TaxTypeNotFound(tax_key)

    changes.sort(key=lambda c: c["detected_at"], reverse=True)
    documents.sort(key=lambda d: d["last_updated"] or "", reverse=True)

    return {
        "key": tax_key,
        "name": tax_name or UNCLASSIFIED.name_zh,
        "countries": sorted(countries),
        "last_updated": latest_overall.isoformat() if latest_overall else None,
        "statistics": {
            "document_count": len(documents),
            "version_count": sum(d["version_count"] for d in documents),
            "change_count": len(changes),
            "analysed_count": len(confidence_values),
            "average_confidence": (
                round(sum(confidence_values) / len(confidence_values), 3)
                if confidence_values else None
            ),
        },
        "documents": documents,
        "changes": changes,
    }


def _documents_with_sources(session: Session) -> list[tuple[Document, Source]]:
    return (
        session.query(Document, Source)
        .join(Source, Document.source_id == Source.id)
        .all()
    )


def _status(recent: int, critical: int) -> str:
    if critical:
        return "critical"
    if recent:
        return "changed"
    return "stable"
