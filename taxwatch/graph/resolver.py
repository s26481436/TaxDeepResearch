"""Resolve citation entity_keys to LegalEntity records, creating if needed."""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from taxwatch.graph.citation import Citation
from taxwatch.models import DocType, LegalEntity


def resolve_entity(session: Session, entity_key: str, title: str = "") -> LegalEntity:
    """Find or create a LegalEntity by key."""
    normalized_key = normalize_entity_key(entity_key)
    entity = session.query(LegalEntity).filter_by(entity_key=normalized_key).first()
    if entity:
        return entity

    entity_type = _infer_type(normalized_key)
    entity = LegalEntity(
        entity_key=normalized_key,
        entity_type=entity_type,
        canonical_title=title or normalized_key,
    )
    session.add(entity)
    session.flush()
    return entity


def resolve_citation(session: Session, citation: Citation) -> LegalEntity:
    return resolve_entity(session, citation.entity_key, citation.raw_text)


def normalize_entity_key(key: str) -> str:
    key = re.sub(r"\s+", "", key)
    key = key.replace("臺", "台")
    key = key.replace("　", "")
    return key


def _infer_type(key: str) -> DocType:
    if re.search(r"法#|條例#|法$|條例$", key):
        return DocType.STATUTE
    if re.search(r"準則|辦法|規則|細則", key):
        return DocType.REGULATION
    if "台財稅" in key:
        return DocType.RULING
    if "釋字" in key or "憲判字" in key:
        return DocType.INTERPRETATION
    return DocType.RULING
