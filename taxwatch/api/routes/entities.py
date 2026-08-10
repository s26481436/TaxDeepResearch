"""Legal-graph entity endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from taxwatch.db import get_session
from taxwatch.graph.relations import get_entity_context, get_impact_spread

router = APIRouter(prefix="/api/entities", tags=["entities"])


@router.get("/{entity_key}/context")
def entity_context(entity_key: str) -> dict[str, Any]:
    session = get_session()
    try:
        ctx = get_entity_context(session, entity_key)
        if not ctx:
            raise HTTPException(404, f"Entity not found: {entity_key}")
        return {
            "entity": {
                "key": ctx["entity"].entity_key,
                "title": ctx["entity"].canonical_title,
            },
            "parent_laws": [
                {"relation": r.relation_type.value, "key": e.entity_key,
                 "title": e.canonical_title}
                for r, e in ctx["parent_laws"]
            ],
            "children": [
                {"relation": r.relation_type.value, "key": e.entity_key,
                 "title": e.canonical_title}
                for r, e in ctx["children"]
            ],
            "siblings": [
                {"key": e.entity_key, "title": e.canonical_title}
                for e in ctx["siblings"]
            ],
        }
    finally:
        session.close()


@router.get("/{entity_key}/impact")
def entity_impact(
    entity_key: str,
    max_depth: int = Query(3, ge=1, le=10),
) -> list[dict[str, Any]]:
    session = get_session()
    try:
        return [
            {"key": e.entity_key, "title": e.canonical_title, "type": e.entity_type.value}
            for e in get_impact_spread(session, entity_key, max_depth)
        ]
    finally:
        session.close()
