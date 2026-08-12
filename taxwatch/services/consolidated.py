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

    When the requested document is a child (e.g. 实施条例), the view walks UP
    to the parent statute first — so the parent's articles are shown with both
    the child's and grandchildren's provisions as supplements. This is what
    ``extract_for_document`` needs: a single call produces the full family view
    regardless of which level the user named.

    Raises DocumentNotFound if nothing matches `external_id`.
    """
    doc = find_document(session, external_id)
    snapshot = _latest_snapshot(session, doc.id)
    articles = _provisions(session, snapshot.id) if snapshot else []

    doc_key = _document_key(articles, doc)

    # If this document is a child, walk up to the parent so the consolidated
    # view starts from the statute (母法) rather than the regulation.
    root_doc, root_key, unreachable_parent = _find_root(session, doc, doc_key)
    parent_status: str | None = "missing" if unreachable_parent else None
    if root_doc.id != doc.id:
        root_snapshot = _latest_snapshot(session, root_doc.id)
        root_articles = _provisions(session, root_snapshot.id) if root_snapshot else []
        if root_articles:
            doc = root_doc
            snapshot = root_snapshot
            articles = root_articles
            doc_key = root_key
        else:
            # The statute is on file but its body never parsed, so it cannot
            # anchor anything. Distinct from missing: refetching is the fix,
            # not re-crawling a source we already have.
            unreachable_parent, parent_status = root_key, "unparsed"

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

    anchored_nodes = {s.node_key for article in consolidated for s in article.supplements}
    unanchored = [s for s in supplements.get(doc_key, []) if s.node_key not in anchored_nodes]

    family = get_family(session, doc_key)
    children = family["children"]

    return {
        "external_id": doc.external_id,
        "title": doc.title,
        "url": doc.url,
        "entity_key": doc_key,
        "as_of": snapshot.dated_at.isoformat() if snapshot else None,
        "official_date": bool(snapshot and snapshot.has_official_date),
        "child_documents": [{"key": e.entity_key, "title": e.canonical_title} for e in children],
        # None when the view is rooted at a statute. Otherwise the 母法 this
        # view is standing in for, and why it could not be used.
        "missing_parent": (
            None
            if unreachable_parent is None
            else {"key": unreachable_parent, "status": parent_status}
        ),
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


def _find_root(session: Session, doc: Document, doc_key: str) -> tuple[Document, str, str | None]:
    """Walk from a child document up to the root statute.

    Returns (root_document, root_key, unreachable_parent_key). The third value
    is the parent the walk knows about but could not follow — because that
    statute has no record in the database. It is the difference between "this
    *is* the 母法" and "this is a 子法 standing in for a 母法 we never fetched",
    which callers must not confuse.
    """
    from taxwatch.graph.hierarchy import derive_parent_key

    visited: set[str] = {doc_key}
    current_doc, current_key = doc, doc_key

    for _ in range(5):
        family = get_family(session, current_key)
        parents = family["parents"]

        # Also try title-derived parent if graph has no edges yet.
        parent_key = derive_parent_key(current_key)

        if parents:
            parent_entity = parents[0]
            parent_doc = _document_for_key(session, parent_entity.entity_key)
            if parent_doc is not None and parent_entity.entity_key not in visited:
                visited.add(parent_entity.entity_key)
                current_doc, current_key = parent_doc, parent_entity.entity_key
                continue
            if parent_doc is None:
                return current_doc, current_key, parent_entity.entity_key
        elif parent_key and parent_key not in visited:
            parent_doc = _document_for_key(session, parent_key)
            if parent_doc is not None:
                visited.add(parent_key)
                current_doc, current_key = parent_doc, parent_key
                continue
            return current_doc, current_key, parent_key

        break

    return current_doc, current_key, None


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
