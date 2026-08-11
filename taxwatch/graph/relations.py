"""Legal relation storage and impact-spread queries."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from taxwatch.graph.citation import Citation
from taxwatch.graph.resolver import resolve_citation, resolve_entity
from taxwatch.models import (
    ExtractionMethod,
    LegalEntity,
    LegalRelation,
    RelationType,
)


def upsert_relation(
    session: Session,
    from_key: str,
    to_key: str,
    relation_type: RelationType,
    confidence: float = 1.0,
    evidence_text: str = "",
    extracted_by: ExtractionMethod = ExtractionMethod.REGEX,
    change_id: int | None = None,
) -> LegalRelation:
    from_entity = resolve_entity(session, from_key)
    to_entity = resolve_entity(session, to_key)

    existing = (
        session.query(LegalRelation)
        .filter_by(
            from_entity_id=from_entity.id,
            to_entity_id=to_entity.id,
            relation_type=relation_type,
        )
        .first()
    )

    if existing:
        if confidence > existing.confidence:
            existing.confidence = confidence
            existing.evidence_text = evidence_text
            existing.extracted_by = extracted_by
        return existing

    rel = LegalRelation(
        from_entity_id=from_entity.id,
        to_entity_id=to_entity.id,
        relation_type=relation_type,
        confidence=confidence,
        evidence_text=evidence_text,
        extracted_by=extracted_by,
        source_change_id=change_id,
    )
    session.add(rel)
    session.flush()
    return rel


def store_citations(
    session: Session,
    source_entity_key: str,
    citations: list[Citation],
    change_id: int | None = None,
) -> list[LegalRelation]:
    relations: list[LegalRelation] = []
    for cit in citations:
        target_entity = resolve_citation(session, cit)
        rel_type = _map_relation_type(cit.relation_type)
        ext_method = ExtractionMethod(cit.extracted_by)

        rel = upsert_relation(
            session,
            from_key=source_entity_key,
            to_key=target_entity.entity_key,
            relation_type=rel_type,
            confidence=cit.confidence,
            evidence_text=cit.raw_text,
            extracted_by=ext_method,
            change_id=change_id,
        )
        relations.append(rel)
    return relations


def get_entity_context(session: Session, entity_key: str) -> dict[str, Any] | None:
    """Get full context for an entity: parent laws, children, siblings."""
    from taxwatch.graph.resolver import normalize_entity_key

    normalized = normalize_entity_key(entity_key)
    entity = session.query(LegalEntity).filter_by(entity_key=normalized).first()
    if not entity:
        return None

    parent_rels = (
        session.query(LegalRelation, LegalEntity)
        .join(LegalEntity, LegalRelation.to_entity_id == LegalEntity.id)
        .filter(LegalRelation.from_entity_id == entity.id)
        .filter(
            LegalRelation.relation_type.in_(
                [
                    RelationType.INTERPRETS,
                    RelationType.AUTHORITY_OF,
                ]
            )
        )
        .all()
    )

    child_rels = (
        session.query(LegalRelation, LegalEntity)
        .join(LegalEntity, LegalRelation.from_entity_id == LegalEntity.id)
        .filter(LegalRelation.to_entity_id == entity.id)
        .filter(
            LegalRelation.relation_type.in_(
                [
                    RelationType.INTERPRETS,
                    RelationType.AUTHORITY_OF,
                ]
            )
        )
        .all()
    )

    parent_keys = {ent.entity_key for _, ent in parent_rels}
    siblings: list[LegalEntity] = []
    for parent_key in parent_keys:
        parent = session.query(LegalEntity).filter_by(entity_key=parent_key).first()
        if parent:
            sibling_rels = (
                session.query(LegalEntity)
                .join(LegalRelation, LegalRelation.from_entity_id == LegalEntity.id)
                .filter(LegalRelation.to_entity_id == parent.id)
                .filter(LegalEntity.id != entity.id)
                .all()
            )
            siblings.extend(sibling_rels)

    return {
        "entity": entity,
        "parent_laws": parent_rels,
        "children": child_rels,
        "siblings": siblings,
    }


def get_impact_spread(session: Session, entity_key: str, max_depth: int = 3) -> list[LegalEntity]:
    """Find all entities affected by a change to the given entity (recursive CTE)."""
    from taxwatch.graph.resolver import normalize_entity_key

    normalized = normalize_entity_key(entity_key)
    entity = session.query(LegalEntity).filter_by(entity_key=normalized).first()
    if not entity:
        return []

    cte_sql = text("""
        WITH RECURSIVE impact AS (
            SELECT from_entity_id AS entity_id, 1 AS depth
            FROM legal_relations
            WHERE to_entity_id = :root_id
            UNION
            SELECT lr.from_entity_id, impact.depth + 1
            FROM legal_relations lr
            JOIN impact ON lr.to_entity_id = impact.entity_id
            WHERE impact.depth < :max_depth
        )
        SELECT DISTINCT entity_id FROM impact
    """)

    result = session.execute(cte_sql, {"root_id": entity.id, "max_depth": max_depth})
    entity_ids = [row[0] for row in result]

    if not entity_ids:
        return []

    return session.query(LegalEntity).filter(LegalEntity.id.in_(entity_ids)).all()


def _map_relation_type(s: str) -> RelationType:
    mapping = {
        "interprets": RelationType.INTERPRETS,
        "authority_of": RelationType.AUTHORITY_OF,
        "amends": RelationType.AMENDS,
        "supersedes": RelationType.SUPERSEDES,
        "cites": RelationType.CITES,
    }
    return mapping.get(s, RelationType.CITES)
