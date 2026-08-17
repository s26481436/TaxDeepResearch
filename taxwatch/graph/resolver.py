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


# Stripped only as a leading prefix — 中华人民共和国 appearing mid-title is part
# of the name proper (e.g. 中华人民共和国政府和新加坡共和国政府...协定).
_PRC_PREFIX_RE = re.compile(r"^中华人民共和国(?=.)")


def normalize_entity_key(key: str) -> str:
    key = re.sub(r"\s+", "", key)
    key = key.replace("臺", "台")
    key = key.replace("　", "")
    key = key.strip("《》")
    # The official long form and the working name are the same law. Left apart,
    # 中华人民共和国增值税法实施条例 never finds its parent 增值税法, because the
    # parent derived from its title carries a prefix the citing text omits.
    return _PRC_PREFIX_RE.sub("", key, count=1)


def derive_document_entity_key(
    title: str = "",
    provisions: list[Any] | None = None,
    external_id: str = "",
) -> str:
    """The canonical graph key standing for a document as a whole.

    Prefers the stem of provision node_keys when available (so the document node
    and its article nodes share an identical stem), falling back to normalized
    title or external_id.
    """
    if provisions:
        for prov in provisions:
            node_key = getattr(prov, "node_key", None)
            if isinstance(prov, tuple) and len(prov) > 0:
                node_key = prov[0]
            if node_key and isinstance(node_key, str):
                stem = node_key.split("#", 1)[0].strip()
                if stem:
                    return normalize_entity_key(stem)
    return normalize_entity_key(title or external_id)


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
