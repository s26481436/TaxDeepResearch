"""Main detection pipeline: discover → fetch → snapshot → diff → graph → analyze → report."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from sqlalchemy.orm import Session

from taxwatch.config import get_settings, load_sources
from taxwatch.connectors.registry import get_connector
from taxwatch.diff.classify import classify_severity
from taxwatch.diff.engine import diff_provisions
from taxwatch.graph.citation import extract_citations
from taxwatch.graph.relations import store_citations
from taxwatch.models import (
    Change,
    ChangeType,
    DocType,
    Document,
    ProvisionNode,
    Snapshot,
    Source,
)
from taxwatch.normalize.base import ProvisionData
from taxwatch.normalize.registry import get_normalizer

logger = logging.getLogger(__name__)


def run_pipeline(
    source_key: str | None = None,
    run_all: bool = False,
    stop_after: str | None = None,
):
    from taxwatch.jobs.runner import LocalRunner

    runner = LocalRunner()
    sources = load_sources()

    if run_all:
        keys = [k for k, v in sources.items() if v.get("enabled", True)]
    elif source_key:
        keys = [source_key]
    else:
        raise ValueError("Specify source_key or run_all=True")

    for key in keys:
        logger.info("Running pipeline for source: %s", key)
        runner.submit(
            job_type="pipeline",
            source_key=key,
            stop_after=stop_after,
        )


STAGES = ["fetch", "diff", "graph", "analyze"]


def execute_pipeline(
    session: Session,
    source_key: str,
    stop_after: str | None = None,
    **_kwargs: Any,
) -> dict:
    stats: dict[str, Any] = {"source": source_key, "stages": {}}
    sources_cfg = load_sources()

    if source_key not in sources_cfg:
        raise ValueError(f"Unknown source: {source_key}")

    cfg = sources_cfg[source_key]
    source = _ensure_source(session, source_key, cfg)

    connector = get_connector(cfg["connector"], cfg.get("config", {}))
    normalizer = get_normalizer(cfg["connector"])

    # Stage: fetch
    logger.info("[%s] Discovering documents...", source_key)
    refs = connector.discover()
    stats["stages"]["discover"] = {"documents_found": len(refs)}
    logger.info("[%s] Found %d documents", source_key, len(refs))

    new_snapshots = 0
    unchanged = 0
    all_changes: list[Change] = []

    for ref in refs:
        doc = _ensure_document(session, source, ref)

        raw = connector.fetch(ref)
        if raw.metadata.get("skip"):
            continue
        _save_raw(raw, source_key)

        normalized = normalizer.normalize(raw)
        full_text = "\n".join(p.text for p in normalized.provisions)
        content_hash = hashlib.sha256(full_text.encode()).hexdigest()

        latest = (
            session.query(Snapshot)
            .filter_by(document_id=doc.id)
            .order_by(Snapshot.fetched_at.desc())
            .first()
        )

        if latest and latest.content_hash == content_hash:
            unchanged += 1
            continue

        snapshot = Snapshot(
            document_id=doc.id,
            content_hash=content_hash,
            raw_path=f"data/raw/{source_key}/{ref.external_id}",
        )
        session.add(snapshot)
        session.flush()

        for prov in normalized.provisions:
            text_hash = hashlib.sha256(prov.text.encode()).hexdigest()
            session.add(
                ProvisionNode(
                    snapshot_id=snapshot.id,
                    node_key=prov.node_key,
                    heading=prov.heading,
                    text=prov.text,
                    text_hash=text_hash,
                )
            )
        session.flush()
        new_snapshots += 1

        if stop_after == "fetch":
            continue

        # Stage: diff
        if latest:
            old_provisions = _load_provisions(session, latest.id)
            changes = _run_diff(
                session,
                doc,
                latest,
                snapshot,
                old_provisions,
                normalized.provisions,
            )
            all_changes.extend(changes)

        if stop_after == "diff":
            continue

        # Stage: graph
        for prov in normalized.provisions:
            citations = extract_citations(prov.text)
            if citations:
                store_citations(session, prov.node_key, citations)

        if stop_after == "graph":
            continue

    session.commit()

    stats["stages"]["fetch"] = {
        "new_snapshots": new_snapshots,
        "unchanged": unchanged,
    }

    if stop_after not in ("fetch", "diff", "graph") and all_changes:
        # Stage: analyze
        analyzed = 0
        from taxwatch.analysis.analyze import analyze_change
        from taxwatch.models import Severity

        for change in all_changes:
            if change.severity == Severity.COSMETIC:
                continue
            try:
                analyze_change(session, change)
                analyzed += 1
            except Exception:
                logger.exception("Failed to analyze change %d", change.id)
        session.commit()
        stats["stages"]["analyze"] = {"analyzed": analyzed}

    stats["total_changes"] = len(all_changes)
    logger.info("[%s] Pipeline complete: %d changes detected", source_key, len(all_changes))
    return stats


def _ensure_source(session: Session, key: str, cfg: dict) -> Source:
    source = session.query(Source).filter_by(key=key).first()
    if not source:
        source = Source(
            key=key,
            country=cfg["country"],
            connector=cfg["connector"],
            description=cfg.get("description", ""),
            config=cfg.get("config", {}),
            enabled=cfg.get("enabled", True),
        )
        session.add(source)
        session.flush()
    return source


def _ensure_document(session: Session, source: Source, ref: Any) -> Document:
    doc = (
        session.query(Document).filter_by(source_id=source.id, external_id=ref.external_id).first()
    )
    if not doc:
        doc = Document(
            source_id=source.id,
            external_id=ref.external_id,
            doc_type=(
                DocType(ref.doc_type)
                if ref.doc_type in DocType.__members__.values()
                else DocType.STATUTE
            ),
            title=ref.title,
            url=ref.url,
            issued_at=ref.issued_at,
        )
        session.add(doc)
        session.flush()
    return doc


def _save_raw(raw: Any, source_key: str):
    settings = get_settings()
    raw_dir = settings.data_dir / "raw" / source_key
    raw_dir.mkdir(parents=True, exist_ok=True)
    safe_name = raw.external_id.replace("/", "_").replace("\\", "_")[:200]
    (raw_dir / safe_name).write_bytes(raw.content)


def _load_provisions(session: Session, snapshot_id: int) -> list[ProvisionData]:
    nodes = session.query(ProvisionNode).filter_by(snapshot_id=snapshot_id).all()
    return [ProvisionData(node_key=n.node_key, heading=n.heading, text=n.text) for n in nodes]


def _run_diff(
    session: Session,
    doc: Document,
    old_snapshot: Snapshot,
    new_snapshot: Snapshot,
    old_provisions: list[ProvisionData],
    new_provisions: list[ProvisionData],
) -> list[Change]:
    diffs = diff_provisions(old_provisions, new_provisions)
    changes: list[Change] = []

    for d in diffs:
        severity = classify_severity(d)
        change = Change(
            document_id=doc.id,
            from_snapshot_id=old_snapshot.id,
            to_snapshot_id=new_snapshot.id,
            node_key=d.node_key,
            change_type=ChangeType(d.change_type),
            diff_text=d.diff_text,
            severity=severity,
        )
        session.add(change)
        changes.append(change)

    session.flush()
    return changes
