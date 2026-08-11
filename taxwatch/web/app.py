"""FastAPI + Jinja2 dashboard.

Mounts the JSON API alongside server-rendered pages so a single process
serves both. Page handlers call the service layer directly — no internal
HTTP round-trip.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from taxwatch.api.routes import ALL_ROUTERS
from taxwatch.config import load_sources
from taxwatch.db import get_session
from taxwatch.services import consolidated as consolidated_svc
from taxwatch.services import dashboard as dashboard_svc
from taxwatch.services import documents as documents_svc
from taxwatch.services import tax_types as tax_types_svc

TEMPLATES_DIR = Path(__file__).parent / "templates"

app = FastAPI(title="TaxWatch Dashboard", version="0.1.0")

for _router in ALL_ROUTERS:
    app.include_router(_router)


@app.on_event("startup")
def _startup():
    from taxwatch.db import init_db

    init_db()


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["severity_class"] = lambda s: {
    "critical": "badge-critical",
    "major": "badge-major",
    "minor": "badge-minor",
    "cosmetic": "badge-muted",
}.get(s, "badge-muted")
templates.env.filters["shortdate"] = lambda s: (s or "")[:10]


def _page(request: Request, template: str, **ctx: Any) -> HTMLResponse:
    return templates.TemplateResponse(request, template, ctx)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, days: int = Query(7, ge=1, le=365)) -> HTMLResponse:
    session = get_session()
    try:
        return _page(
            request,
            "dashboard.html",
            active="dashboard",
            days=days,
            stats=dashboard_svc.get_stats(session, recent_days=days),
            changes=dashboard_svc.list_changes(session, days=days, limit=15),
            tax_types=tax_types_svc.list_tax_types(session, recent_days=days),
            health=dashboard_svc.get_run_health(session),
        )
    finally:
        session.close()


@app.get("/tax-types", response_class=HTMLResponse)
def tax_types_page(request: Request, days: int = Query(30, ge=1, le=365)) -> HTMLResponse:
    session = get_session()
    try:
        return _page(
            request,
            "tax_types.html",
            active="tax-types",
            days=days,
            tax_types=tax_types_svc.list_tax_types(session, recent_days=days),
        )
    finally:
        session.close()


@app.get("/tax-types/{tax_key}", response_class=HTMLResponse)
def tax_type_detail(
    request: Request,
    tax_key: str,
    days: int = Query(90, ge=1, le=3650),
) -> HTMLResponse:
    session = get_session()
    try:
        summary = tax_types_svc.get_summary(session, tax_key, recent_days=days)
    except tax_types_svc.TaxTypeNotFound:
        return _page(
            request, "not_found.html", active="tax-types", message=f"找不到稅種：{tax_key}"
        )
    finally:
        session.close()
    return _page(request, "tax_detail.html", active="tax-types", days=days, summary=summary)


@app.get("/documents", response_class=HTMLResponse)
def documents_page(
    request: Request,
    country: str | None = None,
    tax_key: str | None = None,
) -> HTMLResponse:
    session = get_session()
    try:
        return _page(
            request,
            "documents.html",
            active="documents",
            country=country,
            tax_key=tax_key,
            documents=documents_svc.list_documents(session, country=country, tax_key=tax_key),
        )
    finally:
        session.close()


@app.get("/documents/{external_id}", response_class=HTMLResponse)
def document_history_page(
    request: Request,
    external_id: str,
    compare_from: str | None = None,
    compare_to: str | None = None,
) -> HTMLResponse:
    session = get_session()
    try:
        history = documents_svc.get_history(session, external_id)
        diff = None
        if compare_from and compare_to:
            diff = documents_svc.get_diff(
                session,
                external_id,
                _parse_date(compare_from),
                _parse_date(compare_to),
            )
        return _page(
            request,
            "document_history.html",
            active="documents",
            history=history,
            diff=diff,
            compare_from=compare_from,
            compare_to=compare_to,
        )
    except (documents_svc.DocumentNotFound, documents_svc.SnapshotNotFound) as exc:
        return _page(
            request, "not_found.html", active="documents", message=f"找不到法規版本：{exc}"
        )
    finally:
        session.close()


@app.get("/documents/{external_id}/consolidated", response_class=HTMLResponse)
def document_consolidated_page(request: Request, external_id: str) -> HTMLResponse:
    """The statute as it currently reads, with implementing provisions inline."""
    session = get_session()
    try:
        return _page(
            request,
            "document_consolidated.html",
            active="documents",
            view=consolidated_svc.get_consolidated(session, external_id),
        )
    except documents_svc.DocumentNotFound as exc:
        return _page(request, "not_found.html", active="documents", message=f"找不到法規：{exc}")
    finally:
        session.close()


@app.get("/changes", response_class=HTMLResponse)
def changes_page(
    request: Request,
    # This is the audit view — multi-year lookbacks are the point, so it is
    # not capped at a year the way the "recent activity" dashboard is.
    days: int = Query(30, ge=1, le=3650),
    country: str | None = None,
    tax_key: str | None = None,
    severity: str | None = None,
) -> HTMLResponse:
    session = get_session()
    try:
        return _page(
            request,
            "changes.html",
            active="changes",
            days=days,
            country=country,
            tax_key=tax_key,
            severity=severity,
            changes=dashboard_svc.list_changes(
                session,
                days=days,
                country=country,
                tax_key=tax_key,
                severity=severity,
                limit=200,
            ),
        )
    finally:
        session.close()


@app.get("/changes/{change_id}", response_class=HTMLResponse)
def change_detail_page(request: Request, change_id: int) -> HTMLResponse:
    session = get_session()
    try:
        detail = dashboard_svc.get_change_detail(session, change_id)
    except dashboard_svc.ChangeNotFound:
        return _page(
            request, "not_found.html", active="changes", message=f"找不到異動 #{change_id}"
        )
    finally:
        session.close()
    return _page(request, "change_detail.html", active="changes", change=detail)


@app.get("/runs", response_class=HTMLResponse)
def runs_page(request: Request) -> HTMLResponse:
    session = get_session()
    try:
        return _page(
            request,
            "runs.html",
            active="runs",
            health=dashboard_svc.get_run_health(session),
            runs=dashboard_svc.list_runs(session, limit=50),
        )
    finally:
        session.close()


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    from taxwatch.config import get_settings

    settings = get_settings()
    try:
        sources = load_sources()
    except (OSError, ValueError):
        sources = {}

    session = get_session()
    try:
        from taxwatch.corpus import store as corpus_store

        corpora = corpus_store.stats(session)
    except Exception:  # noqa: BLE001 — corpus table may not exist yet
        corpora = []
    finally:
        session.close()

    return _page(
        request,
        "settings.html",
        active="settings",
        sources=sources,
        corpora=corpora,
        llm={"base_url": settings.llm_base_url, "model": settings.llm_model},
        brave={
            "enabled": settings.brave_search_enabled,
            "configured": bool(settings.brave_search_api_key),
            "max_results": settings.brave_search_max_results,
        },
        email={"configured": bool(settings.smtp_host), "to": settings.email_to},
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _parse_date(value: str) -> date:
    return datetime.fromisoformat(value).date()
