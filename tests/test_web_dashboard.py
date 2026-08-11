"""Smoke tests for the server-rendered dashboard and the JSON API."""

import pytest
from fastapi.testclient import TestClient

from taxwatch.api.routes import dashboard as dashboard_route
from taxwatch.api.routes import documents as documents_route
from taxwatch.api.routes import entities as entities_route
from taxwatch.api.routes import runs as runs_route
from taxwatch.api.routes import tax_types as tax_types_route
from taxwatch.web import app as web_app

_SESSION_MODULES = (
    web_app,
    dashboard_route,
    documents_route,
    entities_route,
    runs_route,
    tax_types_route,
)


@pytest.fixture
def client(session, seeded, monkeypatch):
    """Point every request at the seeded in-memory session."""
    monkeypatch.setattr(session, "close", lambda: None)
    for module in _SESSION_MODULES:
        monkeypatch.setattr(module, "get_session", lambda: session)
    return TestClient(web_app.app)


# ---------- pages ----------


def test_dashboard_page(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "儀表板" in resp.text
    assert "企業所得稅" in resp.text


def test_tax_types_page(client):
    resp = client.get("/tax-types")
    assert resp.status_code == 200
    assert "企業所得稅" in resp.text


def test_tax_detail_page(client):
    resp = client.get("/tax-types/enterprise_income?days=3650")
    assert resp.status_code == 200
    assert "中华人民共和国企业所得税法" in resp.text
    assert "小型微利企业税率由15%调整为25%。" in resp.text


def test_tax_detail_unknown_renders_not_found_page(client):
    resp = client.get("/tax-types/vat")
    assert resp.status_code == 200
    assert "找不到" in resp.text


def test_documents_page(client):
    resp = client.get("/documents")
    assert resp.status_code == 200
    assert "cn-enterprise-income-tax-law" in resp.text


def test_document_history_page(client):
    resp = client.get("/documents/cn-enterprise-income-tax-law")
    assert resp.status_code == 200
    assert "v1" in resp.text and "v3" in resp.text


def test_document_history_page_with_diff(client):
    resp = client.get(
        "/documents/cn-enterprise-income-tax-law",
        params={"compare_from": "2020-06-01", "compare_to": "2026-12-31"},
    )
    assert resp.status_code == 200
    assert "减按20%" in resp.text
    assert "减按25%" in resp.text


def test_document_history_unknown(client):
    resp = client.get("/documents/no-such-law")
    assert resp.status_code == 200
    assert "找不到" in resp.text


def test_changes_page(client):
    resp = client.get("/changes?days=3650")
    assert resp.status_code == 200
    assert "企业所得税法#28" in resp.text


def test_change_detail_page(client, seeded):
    resp = client.get(f"/changes/{seeded['changes'][1].id}")
    assert resp.status_code == 200
    assert "深度分析" in resp.text
    assert "2026-01-01" in resp.text


def test_change_detail_missing(client):
    resp = client.get("/changes/99999")
    assert resp.status_code == 200
    assert "找不到" in resp.text


def test_runs_page(client):
    resp = client.get("/runs")
    assert resp.status_code == 200
    assert "cn-chinatax" in resp.text


def test_settings_page(client):
    resp = client.get("/settings")
    assert resp.status_code == 200
    assert "Brave Search" in resp.text


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


# ---------- JSON API ----------


def test_api_tax_types(client):
    body = client.get("/api/tax-types").json()
    assert body["count"] == 1
    assert body["tax_types"][0]["key"] == "enterprise_income"


def test_api_tax_type_detail(client):
    body = client.get("/api/tax-types/enterprise_income", params={"recent_days": 3650}).json()
    assert body["statistics"]["version_count"] == 3


def test_api_tax_type_404(client):
    assert client.get("/api/tax-types/vat").status_code == 404


def test_api_document_history(client):
    body = client.get("/api/documents/cn-enterprise-income-tax-law/history").json()
    assert body["version_count"] == 3


def test_api_document_at_date(client):
    body = client.get("/api/documents/cn-enterprise-income-tax-law/at/2024-01-01").json()
    assert body["snapshot_date"].startswith("2023-06-01")


def test_api_document_diff(client):
    body = client.get(
        "/api/documents/cn-enterprise-income-tax-law/diff",
        params={"from": "2020-06-01", "to": "2026-12-31"},
    ).json()
    assert body["summary"]["modified"] == 1
    assert body["summary"]["added"] == 1


def test_api_document_404(client):
    assert client.get("/api/documents/nope/history").status_code == 404


def test_api_stats(client):
    assert client.get("/api/stats").json()["document_count"] == 1


def test_api_change_detail(client, seeded):
    body = client.get(f"/api/changes/{seeded['changes'][1].id}").json()
    assert body["analysis"]["confidence"] == 0.9


def test_api_runs(client):
    body = client.get("/api/runs").json()
    assert body["health"]["total"] == 1


def test_api_changes_list(client):
    """Regression: the changes list must be reachable from the dashboard app,
    not only from the JSON-only app."""
    body = client.get("/api/changes", params={"days": 3650}).json()
    assert len(body) == 2


def test_api_entity_context_404(client):
    assert client.get("/api/entities/no-such-law/context").status_code == 404


def test_both_apps_expose_the_same_api_surface():
    """The dashboard is what `taxwatch serve` runs, so it must not be missing
    endpoints the JSON-only app has."""
    from taxwatch.api.main import app as api_app

    def api_paths(app):
        return {
            (route.path, frozenset(route.methods))
            for route in app.routes
            if getattr(route, "path", "").startswith("/api/")
        }

    assert api_paths(api_app) == api_paths(web_app.app)
