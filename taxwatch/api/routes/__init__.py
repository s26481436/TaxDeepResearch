"""JSON API routers.

`ALL_ROUTERS` is the single source of truth for the API surface — both the
JSON-only app and the dashboard app mount exactly this list, so an endpoint
can never exist on one and be missing from the other.
"""

from __future__ import annotations

from fastapi import APIRouter

from taxwatch.api.routes.dashboard import router as dashboard_router
from taxwatch.api.routes.documents import router as documents_router
from taxwatch.api.routes.entities import router as entities_router
from taxwatch.api.routes.requirements import router as requirements_router
from taxwatch.api.routes.runs import router as runs_router
from taxwatch.api.routes.tax_types import router as tax_types_router

ALL_ROUTERS: list[APIRouter] = [
    tax_types_router,
    documents_router,
    dashboard_router,
    entities_router,
    requirements_router,
    runs_router,
]

__all__ = ["ALL_ROUTERS"]
