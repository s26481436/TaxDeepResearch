"""Dashboard aggregates: headline stats, recent changes, job health."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from taxwatch.corpus.store import make_classifier
from taxwatch.models import (
    Analysis,
    Change,
    Document,
    JobRun,
    JobStatus,
    Snapshot,
    Source,
)


class ChangeNotFound(LookupError):
    """Raised when a change id does not exist."""


def get_stats(session: Session, *, recent_days: int = 7) -> dict[str, Any]:
    cutoff = datetime.utcnow() - timedelta(days=recent_days)

    classify_doc = make_classifier(session)
    tax_keys = {
        classify_doc(title, external_id).key
        for title, external_id in session.query(Document.title, Document.external_id).all()
    }

    recent_changes = session.query(Change).filter(Change.detected_at >= cutoff).count()
    unanalysed = (
        session.query(Change)
        .outerjoin(Analysis, Analysis.change_id == Change.id)
        .filter(Change.detected_at >= cutoff, Analysis.id.is_(None))
        .count()
    )
    confidences = [
        a.confidence
        for a in session.query(Analysis)
        .join(Change, Analysis.change_id == Change.id)
        .filter(Change.detected_at >= cutoff)
        .all()
    ]

    return {
        "recent_days": recent_days,
        "tax_type_count": len(tax_keys),
        "document_count": session.query(Document).count(),
        "snapshot_count": session.query(Snapshot).count(),
        "source_count": session.query(Source).filter_by(enabled=True).count(),
        "recent_changes": recent_changes,
        "pending_review": unanalysed,
        "average_confidence": (
            round(sum(confidences) / len(confidences), 3) if confidences else None
        ),
    }


def list_changes(
    session: Session,
    *,
    days: int = 7,
    country: str | None = None,
    tax_key: str | None = None,
    severity: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    cutoff = datetime.utcnow() - timedelta(days=days)
    query = (
        session.query(Change, Document, Source, Analysis)
        .join(Document, Change.document_id == Document.id)
        .join(Source, Document.source_id == Source.id)
        .outerjoin(Analysis, Analysis.change_id == Change.id)
        .filter(Change.detected_at >= cutoff)
    )
    if country:
        query = query.filter(Source.country == country)
    if severity:
        query = query.filter(Change.severity == severity)

    classify_doc = make_classifier(session)
    rows: list[dict[str, Any]] = []
    for change, doc, source, analysis in query.order_by(Change.detected_at.desc()).all():
        tax_type = classify_doc(doc.title, doc.external_id)
        if tax_key and tax_type.key != tax_key:
            continue
        rows.append(_change_row(change, doc, source, analysis, tax_type))
        if len(rows) >= limit:
            break
    return rows


def get_change_detail(session: Session, change_id: int) -> dict[str, Any]:
    row = (
        session.query(Change, Document, Source, Analysis)
        .join(Document, Change.document_id == Document.id)
        .join(Source, Document.source_id == Source.id)
        .outerjoin(Analysis, Analysis.change_id == Change.id)
        .filter(Change.id == change_id)
        .first()
    )
    if row is None:
        raise ChangeNotFound(str(change_id))

    change, doc, source, analysis = row
    classify_doc = make_classifier(session)
    detail = _change_row(change, doc, source, analysis, classify_doc(doc.title, doc.external_id))
    detail["diff_text"] = change.diff_text
    detail["old_text"], detail["new_text"] = _provision_texts(session, change)
    detail["analysis"] = (
        {
            "summary_zh": analysis.summary_zh,
            "effective_date": analysis.effective_date,
            "affected_parties": analysis.affected_parties,
            "parent_law_impact": analysis.parent_law_impact,
            "confidence": analysis.confidence,
            "citations": analysis.citations,
            "model": analysis.model,
            "created_at": analysis.created_at.isoformat(),
        }
        if analysis
        else None
    )
    return detail


def list_runs(session: Session, *, limit: int = 30) -> list[dict[str, Any]]:
    runs = session.query(JobRun).order_by(JobRun.started_at.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "job_type": r.job_type,
            "trigger": r.trigger.value,
            "source_key": r.source_key,
            "status": r.status.value,
            "started_at": r.started_at.isoformat() if r.started_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "duration_seconds": (
                round((r.finished_at - r.started_at).total_seconds(), 1)
                if r.started_at and r.finished_at
                else None
            ),
            "stats": r.stats,
            "error": r.error,
        }
        for r in runs
    ]


def get_run_health(session: Session, *, limit: int = 100) -> dict[str, Any]:
    runs = session.query(JobRun).order_by(JobRun.started_at.desc()).limit(limit).all()
    if not runs:
        return {"total": 0, "success_rate": None, "failed": 0, "running": 0}
    completed = sum(1 for r in runs if r.status == JobStatus.COMPLETED)
    failed = sum(1 for r in runs if r.status == JobStatus.FAILED)
    running = sum(1 for r in runs if r.status == JobStatus.RUNNING)
    return {
        "total": len(runs),
        "success_rate": round(completed / len(runs), 3),
        "failed": failed,
        "running": running,
    }


def _change_row(change, doc, source, analysis, tax_type) -> dict[str, Any]:
    return {
        "id": change.id,
        "node_key": change.node_key,
        "change_type": change.change_type.value,
        "severity": change.severity.value,
        "detected_at": change.detected_at.isoformat(),
        "document_title": doc.title,
        "external_id": doc.external_id,
        "document_url": doc.url,
        "country": source.country,
        "source_key": source.key,
        "tax_key": tax_type.key,
        "tax_name": tax_type.name_zh,
        "summary": analysis.summary_zh if analysis else "",
        "effective_date": analysis.effective_date if analysis else "",
        "confidence": analysis.confidence if analysis else None,
    }


def _provision_texts(session: Session, change: Change) -> tuple[str, str]:
    from taxwatch.models import ProvisionNode

    old_text = ""
    if change.from_snapshot_id:
        node = (
            session.query(ProvisionNode)
            .filter_by(snapshot_id=change.from_snapshot_id, node_key=change.node_key)
            .first()
        )
        old_text = node.text if node else ""

    node = (
        session.query(ProvisionNode)
        .filter_by(snapshot_id=change.to_snapshot_id, node_key=change.node_key)
        .first()
    )
    return old_text, (node.text if node else "")
