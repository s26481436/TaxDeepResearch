"""Consolidated reading view — a statute together with what implements it.

Reading 增值税法第1条 on its own tells you who owes VAT but not what "销售货物"
covers; that is in 实施条例第3条. The version history answers "what changed in
this document", but nobody asks a tax question about one document — they ask
what the rule *currently is*, which is the statute plus every implementing
provision hanging off it.

This assembles that: each article of the parent, followed by the child
provisions that cite it, drawn from the latest snapshot of each child.

It deliberately does not merge the texts into one synthesised rule. Which
implementing provision governs is a legal judgement, and silently splicing them
would produce authoritative-looking text that no authority ever published. The
provisions are shown attributed and side by side; the reader draws the
conclusion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from taxwatch.graph.hierarchy import get_family
from taxwatch.graph.resolver import normalize_entity_key
from taxwatch.models import (
    Document,
    LegalEntity,
    LegalRelation,
    ProvisionNode,
    RelationType,
    Snapshot,
)
from taxwatch.services.documents import DocumentNotFound, find_document

# Relations that mean "this provision fleshes out that one". CITES is excluded:
# a passing cross-reference is not implementing detail, and including it buries
# the real supplements in noise.
_IMPLEMENTING = (RelationType.AUTHORITY_OF, RelationType.INTERPRETS)

_DATED_AT = func.coalesce(Snapshot.issued_at, Snapshot.fetched_at)


@dataclass
class Supplement:
    """One child provision that expands a parent article."""

    document_title: str
    document_external_id: str
    doc_type: str
    node_key: str
    heading: str
    text: str
    relation: str
    issued_at: str | None


@dataclass
class ConsolidatedArticle:
    node_key: str
    heading: str
    text: str
    supplements: list[Supplement] = field(default_factory=list)


def get_consolidated(session: Session, external_id: str) -> dict[str, Any]:
    """A statute's current text with each article's implementing provisions.

    Raises DocumentNotFound if nothing matches `external_id`.
    """
    doc = find_document(session, external_id)
    snapshot = _latest_snapshot(session, doc.id)
    articles = _provisions(session, snapshot.id) if snapshot else []

    doc_key = _document_key(articles, doc)
    supplements = _supplements_by_article(session, doc_key)

    consolidated = [
        ConsolidatedArticle(
            node_key=p.node_key,
            heading=p.heading,
            text=p.text,
            supplements=supplements.get(normalize_entity_key(p.node_key), []),
        )
        for p in articles
    ]

    # Child provisions written against the document as a whole rather than a
    # numbered article — a 公告 that implements the statute generally. They
    # belong in the view, but under no single article.
    #
    # A 依据 clause usually names both (「根据《消费税法》第四条」), so the same
    # provision arrives here and against the article. The article placement is
    # strictly more informative, so drop the general one when both exist.
    anchored_nodes = {s.node_key for article in consolidated for s in article.supplements}
    unanchored = [s for s in supplements.get(doc_key, []) if s.node_key not in anchored_nodes]

    children = get_family(session, doc_key)["children"]

    return {
        "external_id": doc.external_id,
        "title": doc.title,
        "url": doc.url,
        "entity_key": doc_key,
        "as_of": snapshot.dated_at.isoformat() if snapshot else None,
        "official_date": bool(snapshot and snapshot.has_official_date),
        "child_documents": [{"key": e.entity_key, "title": e.canonical_title} for e in children],
        "statistics": {
            "article_count": len(consolidated),
            "supplemented_count": sum(1 for a in consolidated if a.supplements),
            "supplement_count": sum(len(a.supplements) for a in consolidated) + len(unanchored),
        },
        "articles": [
            {
                "node_key": a.node_key,
                "heading": a.heading,
                "text": a.text,
                "supplements": [_as_dict(s) for s in a.supplements],
            }
            for a in consolidated
        ],
        "unanchored_supplements": [_as_dict(s) for s in unanchored],
    }


# ---------- internals ----------


def _supplements_by_article(session: Session, doc_key: str) -> dict[str, list[Supplement]]:
    """Every provision pointing at this document or one of its articles.

    Keyed by the *target* node key, so an article can look up what implements
    it without walking the whole relation table per article.
    """
    target = session.query(LegalEntity).filter(
        (LegalEntity.entity_key == doc_key) | (LegalEntity.entity_key.like(f"{doc_key}#%"))
    )
    target_ids = {e.id: e.entity_key for e in target}
    if not target_ids:
        return {}

    rows = (
        session.query(LegalRelation, LegalEntity)
        .join(LegalEntity, LegalRelation.from_entity_id == LegalEntity.id)
        .filter(
            LegalRelation.to_entity_id.in_(target_ids),
            LegalRelation.relation_type.in_(_IMPLEMENTING),
        )
        .all()
    )

    # One provision can reach the same article under several relation types
    # (a 依据 clause is both authority and interpretation). Show it once, under
    # the strongest reading.
    strongest: dict[tuple[str, str], tuple[int, Supplement]] = {}
    for relation, source in rows:
        # Skip a document's references to its own articles — those are internal
        # cross-references, not another instrument supplementing this one.
        source_doc_key = source.entity_key.split("#", 1)[0]
        if source_doc_key == doc_key:
            continue

        target_key = target_ids[relation.to_entity_id]
        rank = _IMPLEMENTING.index(relation.relation_type)
        slot = (target_key, source.entity_key)
        if slot in strongest and strongest[slot][0] <= rank:
            continue

        supplement = _build_supplement(session, source, relation)
        if supplement is None:
            continue
        strongest[slot] = (rank, supplement)

    by_article: dict[str, list[Supplement]] = {}
    for (target_key, _), (_, supplement) in strongest.items():
        by_article.setdefault(target_key, []).append(supplement)

    for items in by_article.values():
        items.sort(key=lambda s: (s.document_title, s.node_key))
    return by_article


def _build_supplement(
    session: Session,
    source: LegalEntity,
    relation: LegalRelation,
) -> Supplement | None:
    """Resolve a citing entity back to the provision text it stands for."""
    source_doc_key = source.entity_key.split("#", 1)[0]
    document = _document_for_key(session, source_doc_key)
    if document is None:
        return None

    snapshot = _latest_snapshot(session, document.id)
    if snapshot is None:
        return None

    node = (
        session.query(ProvisionNode)
        .filter_by(snapshot_id=snapshot.id, node_key=source.entity_key)
        .first()
    )
    if node is None:
        return None

    return Supplement(
        document_title=document.title,
        document_external_id=document.external_id,
        doc_type=document.doc_type.value,
        node_key=node.node_key,
        heading=node.heading,
        text=node.text,
        relation=relation.relation_type.value,
        issued_at=snapshot.dated_at.isoformat() if snapshot else None,
    )


def _document_for_key(session: Session, doc_key: str) -> Document | None:
    """Find the Document a graph key stands for.

    The entity records `current_document_id` when the pipeline registered it;
    otherwise fall back to matching the title, which is what the key is derived
    from in the first place.
    """
    entity = session.query(LegalEntity).filter_by(entity_key=doc_key).first()
    if entity is not None and entity.current_document_id:
        document = session.get(Document, entity.current_document_id)
        if document is not None:
            return document

    for candidate in session.query(Document).filter(Document.title.like(f"%{doc_key}%")).all():
        if normalize_entity_key(candidate.title) == doc_key:
            return candidate
    return None


def _document_key(articles: list[ProvisionNode], doc: Document) -> str:
    for node in articles:
        stem = node.node_key.split("#", 1)[0].strip()
        if stem:
            return normalize_entity_key(stem)
    return normalize_entity_key(doc.title or doc.external_id)


def _latest_snapshot(session: Session, document_id: int) -> Snapshot | None:
    return (
        session.query(Snapshot)
        .filter_by(document_id=document_id)
        .order_by(_DATED_AT.desc(), Snapshot.id.desc())
        .first()
    )


def _provisions(session: Session, snapshot_id: int) -> list[ProvisionNode]:
    return (
        session.query(ProvisionNode)
        .filter_by(snapshot_id=snapshot_id)
        .order_by(ProvisionNode.id.asc())
        .all()
    )


def _as_dict(s: Supplement) -> dict[str, Any]:
    return {
        "document_title": s.document_title,
        "external_id": s.document_external_id,
        "doc_type": s.doc_type,
        "node_key": s.node_key,
        "heading": s.heading,
        "text": s.text,
        "relation": s.relation,
        "issued_at": s.issued_at,
    }


__all__ = ["get_consolidated", "DocumentNotFound"]
