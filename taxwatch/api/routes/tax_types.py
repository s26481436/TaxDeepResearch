"""Tax-type status endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from taxwatch.db import get_session
from taxwatch.services import tax_types as svc

router = APIRouter(prefix="/api/tax-types", tags=["tax-types"])


@router.get("")
def list_tax_types(recent_days: int = Query(7, ge=1, le=365)) -> dict[str, Any]:
    session = get_session()
    try:
        rows = svc.list_tax_types(session, recent_days=recent_days)
        return {"count": len(rows), "tax_types": rows}
    finally:
        session.close()


@router.get("/{tax_key}")
def get_tax_type(
    tax_key: str,
    recent_days: int = Query(90, ge=1, le=3650),
) -> dict[str, Any]:
    session = get_session()
    try:
        return svc.get_summary(session, tax_key, recent_days=recent_days)
    except svc.TaxTypeNotFound:
        raise HTTPException(404, f"Tax type not found: {tax_key}") from None
    finally:
        session.close()
