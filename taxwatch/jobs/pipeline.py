"""Main detection pipeline: discover → fetch → snapshot → diff → graph → analyze → report."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from taxwatch.config import get_settings, load_sources
from taxwatch.connectors.registry import get_connector
from taxwatch.diff.classify import classify_severity
from taxwatch.diff.engine import diff_provisions
from taxwatch.graph.citation import extract_citations
from taxwatch.graph.hierarchy import (
    derive_parent_key,
    promote_declared_authority,
    register_document_hierarchy,
)
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
from taxwatch.requirements.staleness import flag_stale_fields

logger = logging.getLogger(__name__)


def run_pipeline(
    source_key: str | None = None,
    run_all: bool = False,
    stop_after: str | None = None,
    tax_keys: list[str] | None = None,
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
            tax_keys=tax_keys,
        )


STAGES = ["fetch", "diff", "graph", "analyze"]


def execute_pipeline(
    session: Session,
    source_key: str,
    stop_after: str | None = None,
    tax_keys: list[str] | None = None,
    **_kwargs: Any,
) -> dict:
    stats: dict[str, Any] = {"source": source_key, "stages": {}}
    sources_cfg = load_sources()

    if source_key not in sources_cfg:
        raise ValueError(f"Unknown source: {source_key}")

    cfg = sources_cfg[source_key]
    source = _ensure_source(session, source_key, cfg)

    # Resolve tax_keys: CLI --tax takes priority, then sources.yaml tax_keys
    if not tax_keys:
        yaml_keys = cfg.get("tax_keys")
        if yaml_keys:
            tax_keys = [k.strip() for k in yaml_keys if k.strip()]

    # Resolve TaxType objects for filtering
    tax_types = None
    if tax_keys:
        from taxwatch.taxonomy import by_key

        tax_types = [by_key(k) for k in tax_keys]
        tax_types = [t for t in tax_types if t is not None]
        if not tax_types:
            tax_types = None

    # Layer 1: inject tax keywords into connector config (if no custom
    # keywords already set) so the connector's existing title filter
    # skips irrelevant documents before fetching detail pages.
    connector_cfg = dict(cfg.get("config", {}))
    if tax_types and not connector_cfg.get("keywords"):
        connector_cfg["keywords"] = [
            kw for t in tax_types for kw in t.keywords
        ]
    connector = get_connector(cfg["connector"], connector_cfg)
    normalizer = get_normalizer(cfg["connector"])

    # Stage: fetch
    logger.info("[%s] Discovering documents...", source_key)
    refs = connector.discover()

    # Layer 2: authoritative filter using the corpus-backed classifier —
    # same function the dashboard uses, so results are consistent.
    filtered_out = 0
    if tax_types:
        from taxwatch.corpus.store import make_classifier

        classify_doc = make_classifier(session)
        wanted = {t.key for t in tax_types}
        before = len(refs)
        refs = [
            r for r in refs
            if classify_doc(r.title, r.external_id).key in wanted
        ]
        filtered_out = before - len(refs)
        logger.info(
            "[%s] Tax filter %s: kept %d, filtered out %d",
            source_key, tax_keys, len(refs), filtered_out,
        )

    discover_stats: dict[str, Any] = {"documents_found": len(refs)}
    if tax_keys:
        discover_stats["tax_filter"] = tax_keys
        discover_stats["filtered_out"] = filtered_out
    stats["stages"]["discover"] = discover_stats
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
            issued_at=_issued_at(ref, normalized),
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
        doc_key = _document_entity_key(normalized, doc)
        register_document_hierarchy(session, doc, doc_key)
        # 公告/部门规章 name their parent in the 依据 clause rather than in their
        # title, so the title-derived link above finds nothing for them.
        promote_declared_authority(session, doc, doc_key, normalized.provisions)
        parent_key = derive_parent_key(doc_key)

        for prov in normalized.provisions:
            # Deictic references (本法第14條) only resolve if we tell the
            # extractor which law this document is a child of.
            citations = extract_citations(
                prov.text,
                parent_key=parent_key,
                self_key=doc_key,
            )
            if citations:
                store_citations(session, prov.node_key, citations)

        if stop_after == "graph":
            continue

    # Guidance built on a provision that just moved is now suspect. Flag the
    # affected cells before analysis, so a run that dies mid-analysis still
    # leaves the compliance matrix honest about what it no longer knows.
    if all_changes:
        stale = flag_stale_fields(session, all_changes)
        stats["stages"]["requirements"] = {"fields_flagged": len(stale)}

    session.commit()

    stats["stages"]["fetch"] = {
        "new_snapshots": new_snapshots,
        "unchanged": unchanged,
    }

    if stop_after not in ("fetch", "diff", "graph") and all_changes:
        # Stage: analyze
        analyzed = 0
        from taxwatch.analysis import brave_search
        from taxwatch.analysis.analyze import analyze_change
        from taxwatch.analysis.evidence import gather_for_document
        from taxwatch.models import Severity

        brave_search.start_run()

        # External corroboration describes the amendment, not the article: a
        # statute revised in fifty places was revised once and announced once.
        # Fetch it per document and share it, or every changed article buys
        # another copy of the same answer.
        by_document: dict[int, list[Change]] = {}
        for change in all_changes:
            if change.severity == Severity.COSMETIC:
                continue
            by_document.setdefault(change.document_id, []).append(change)

        for document_id, changes in by_document.items():
            doc = session.get(Document, document_id)
            try:
                document_evidence = gather_for_document(
                    doc.title if doc else "",
                    doc.external_id if doc else "",
                    doc.issued_at if doc else None,
                    session=session,
                    # The official library is free, so it is always consulted;
                    # this only decides whether a miss there may fall through
                    # to the metered API for this document.
                    allow_metered=_worth_metered_search(changes),
                )
            except Exception:
                logger.exception("Failed to gather evidence for document %d", document_id)
                document_evidence = []

            for change in changes:
                try:
                    analyze_change(session, change, document_evidence=document_evidence)
                    analyzed += 1
                except Exception:
                    logger.exception("Failed to analyze change %d", change.id)

        session.commit()
        stats["stages"]["analyze"] = {
            "analyzed": analyzed,
            "documents": len(by_document),
            "metered_queries": brave_search.get_budget().spent,
        }

    stats["total_changes"] = len(all_changes)
    logger.info("[%s] Pipeline complete: %d changes detected", source_key, len(all_changes))
    return stats


# Ordered least to most serious, so a configured floor admits everything at
# or above it.
_SEVERITY_ORDER = ("cosmetic", "minor", "major", "critical")


def _worth_metered_search(changes: list[Change]) -> bool:
    """May this document's changes fall through to the metered search API?

    Off by default: an empty `brave_search_min_severity` admits everything, so
    coverage is unchanged unless someone opts into rationing. Setting it to
    `major` spends quota only where the corroboration is worth paying for.
    """
    floor = (get_settings().brave_search_min_severity or "").strip().lower()
    if not floor:
        return True
    if floor not in _SEVERITY_ORDER:
        logger.warning("Ignoring unknown brave_search_min_severity: %r", floor)
        return True

    threshold = _SEVERITY_ORDER.index(floor)
    return any(
        _SEVERITY_ORDER.index(c.severity.value) >= threshold
        for c in changes
        if c.severity.value in _SEVERITY_ORDER
    )


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
        doc = session.query(Document).filter_by(source_id=source.id, title=ref.title).first()
        if doc:
            doc.external_id = ref.external_id
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

    # An amended document is re-issued under a new date and sometimes a revised
    # title; leaving the first crawl's values in place freezes it at whatever we
    # happened to see first.
    if ref.title and ref.title != doc.title:
        doc.title = ref.title
    if ref.url and ref.url != doc.url:
        doc.url = ref.url
    if ref.issued_at and ref.issued_at != doc.issued_at:
        doc.issued_at = ref.issued_at
    new_doc_type = (
        DocType(ref.doc_type) if ref.doc_type in DocType.__members__.values() else DocType.STATUTE
    )
    if new_doc_type != doc.doc_type:
        doc.doc_type = new_doc_type
    return doc


def _issued_at(ref: Any, normalized: Any) -> Any:
    """The date the authority put on this version, if it gave one."""
    if getattr(ref, "issued_at", None):
        return ref.issued_at
    meta = getattr(normalized, "metadata", None) or {}
    for key in ("issued_at", "written_date", "pub_date"):
        value = meta.get(key)
        if isinstance(value, datetime):
            return value
    return None


def _document_entity_key(normalized: Any, doc: Document) -> str:
    """The graph key standing for the document as a whole.

    Taken from the provisions' own keys rather than the title, so the document
    node and its article nodes share a stem — otherwise 增值税法实施条例 and
    增值税法实施条例#3 would be unrelated entities.
    """
    from taxwatch.graph.resolver import normalize_entity_key

    for prov in normalized.provisions:
        stem = prov.node_key.split("#", 1)[0].strip()
        if stem:
            return normalize_entity_key(stem)
    return normalize_entity_key(normalized.title or doc.title or doc.external_id)


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
