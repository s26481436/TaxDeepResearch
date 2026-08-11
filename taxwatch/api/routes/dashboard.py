"""Dashboard aggregate endpoints: stats and changes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from taxwatch.db import get_session
from taxwatch.services import dashboard as svc

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/stats")
def get_stats(recent_days: int = Query(7, ge=1, le=3650)) -> dict[str, Any]:
    session = get_session()
    try:
        return svc.get_stats(session, recent_days=recent_days)
    finally:
        session.close()


@router.get("/changes")
def list_changes(
    days: int = Query(7, ge=1, le=3650),
    country: str | None = None,
    tax_key: str | None = None,
    severity: str | None = None,
    limit: int = Query(50, ge=1, le=500),
) -> list[dict[str, Any]]:
    session = get_session()
    try:
        return svc.list_changes(
            session,
            days=days,
            country=country,
            tax_key=tax_key,
            severity=severity,
            limit=limit,
        )
    finally:
        session.close()


@router.get("/changes/{change_id}")
def get_change(change_id: int) -> dict[str, Any]:
    session = get_session()
    try:
        return svc.get_change_detail(session, change_id)
    except svc.ChangeNotFound:
        raise HTTPException(404, f"Change not found: {change_id}") from None
    finally:
        session.close()
