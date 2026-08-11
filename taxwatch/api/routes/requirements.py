"""申報規範 endpoints — the compliance matrix and its review queue."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from taxwatch.db import get_session
from taxwatch.services import requirements as svc

router = APIRouter(prefix="/api/requirements", tags=["requirements"])


@router.get("")
def list_requirements(
    country: str | None = None,
    tax_key: str | None = None,
) -> dict[str, Any]:
    session = get_session()
    try:
        rows = svc.list_requirements(session, country=country, tax_key=tax_key)
        return {"count": len(rows), "requirements": rows}
    finally:
        session.close()


@router.get("/review")
def review_queue(tax_key: str | None = None) -> dict[str, Any]:
    """Cells whose provisions moved, or that were never anchored to one."""
    session = get_session()
    try:
        return svc.review_summary(session, tax_key=tax_key)
    finally:
        session.close()


@router.get("/{requirement_id}")
def get_requirement(requirement_id: int) -> dict[str, Any]:
    session = get_session()
    try:
        return svc.get_requirement(session, requirement_id)
    except svc.RequirementNotFound:
        raise HTTPException(404, f"Requirement not found: {requirement_id}") from None
    finally:
        session.close()


@router.put("/{requirement_id}/fields/{field_key}")
def update_field(
    requirement_id: int,
    field_key: str,
    value: str = Body(..., embed=True),
    clear_flag: bool = Query(True, description="同時清除待覆核標記"),
) -> dict[str, Any]:
    """Record a reviewer's correction. Manual edits survive re-extraction."""
    session = get_session()
    try:
        return svc.update_field(session, requirement_id, field_key, value, clear_flag=clear_flag)
    except svc.RequirementNotFound:
        raise HTTPException(404, f"Requirement not found: {requirement_id}") from None
    finally:
        session.close()
