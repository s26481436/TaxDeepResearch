"""Document version-history endpoints."""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from taxwatch.db import get_session
from taxwatch.services import documents as svc

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.get("")
def list_documents(
    country: str | None = None,
    tax_key: str | None = None,
    limit: int = Query(200, ge=1, le=1000),
) -> dict[str, Any]:
    session = get_session()
    try:
        rows = svc.list_documents(session, country=country, tax_key=tax_key, limit=limit)
        return {"count": len(rows), "documents": rows}
    finally:
        session.close()


@router.get("/{external_id}/history")
def get_history(external_id: str) -> dict[str, Any]:
    session = get_session()
    try:
        return svc.get_history(session, external_id)
    except svc.DocumentNotFound:
        raise HTTPException(404, f"Document not found: {external_id}") from None
    except svc.SnapshotNotFound:
        raise HTTPException(404, f"No snapshots for document: {external_id}") from None
    finally:
        session.close()


@router.get("/{external_id}/at/{at}")
def get_version_at(external_id: str, at: date) -> dict[str, Any]:
    session = get_session()
    try:
        return svc.get_version_at(session, external_id, at)
    except svc.DocumentNotFound:
        raise HTTPException(404, f"Document not found: {external_id}") from None
    except svc.SnapshotNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    finally:
        session.close()


@router.get("/{external_id}/diff")
def get_diff(
    external_id: str,
    from_date: date = Query(..., alias="from"),
    to_date: date = Query(..., alias="to"),
) -> dict[str, Any]:
    session = get_session()
    try:
        return svc.get_diff(session, external_id, from_date, to_date)
    except svc.DocumentNotFound:
        raise HTTPException(404, f"Document not found: {external_id}") from None
    except svc.SnapshotNotFound as exc:
        raise HTTPException(404, str(exc)) from None
    finally:
        session.close()
