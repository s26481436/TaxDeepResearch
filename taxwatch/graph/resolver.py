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
    # TW + CN statutes
    if re.search(r"法#|條例#|条例#|法$|條例$|条例$", key):
        return DocType.STATUTE
    # TW + CN regulations
    if re.search(r"準則|辦法|規則|細則|细则|办法|规则|暂行条例|暂行办法|管理办法", key):
        return DocType.REGULATION
    # TW rulings
    if "台財稅" in key:
        return DocType.RULING
    # TW constitutional interpretations
    if "釋字" in key or "憲判字" in key:
        return DocType.INTERPRETATION
    # CN 文号 (公告/通知/批复)
    if re.search(r"公告\d{4}年|财税[〔\[]|国税[发函]|税总[发函]", key):
        return DocType.RULING
    return DocType.RULING
