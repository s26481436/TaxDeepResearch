"""Tests for the Brave Search evidence layer."""

import httpx
import pytest
import respx

from taxwatch.analysis import brave_search
from taxwatch.analysis.brave_search import (
    build_queries,
    gather_results,
    search,
)
from taxwatch.config import Settings

_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


@pytest.fixture
def enabled_settings(monkeypatch):
    settings = Settings(
        brave_search_api_key="test-key",
        brave_search_enabled=True,
        brave_search_max_results=3,
        brave_search_timeout=5,
    )
    monkeypatch.setattr(brave_search, "get_settings", lambda: settings)
    return settings


@pytest.fixture
def disabled_settings(monkeypatch):
    settings = Settings(brave_search_enabled=False, brave_search_api_key="test-key")
    monkeypatch.setattr(brave_search, "get_settings", lambda: settings)
    return settings


def _payload(*items):
    return {"web": {"results": list(items)}}


@respx.mock
def test_search_parses_results(enabled_settings):
    respx.get(_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json=_payload(
                {
                    "title": "财政部 <strong>税务总局</strong>公告",
                    "description": "关于<strong>小微企业</strong>的公告",
                    "url": "https://example.gov.cn/a",
                    "age": "2 days ago",
                },
            ),
        )
    )
    results = search("企业所得税 第28条")
    assert len(results) == 1
    # HTML highlight markup from the API must be stripped.
    assert results[0].title == "财政部 税务总局公告"
    assert results[0].description == "关于小微企业的公告"
    assert results[0].url == "https://example.gov.cn/a"


@respx.mock
def test_search_drops_results_without_url(enabled_settings):
    respx.get(_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json=_payload(
                {"title": "no url", "description": "x", "url": ""},
                {"title": "ok", "description": "y", "url": "https://example.gov.cn/b"},
            ),
        )
    )
    assert [r.url for r in search("查詢")] == ["https://example.gov.cn/b"]


def test_search_skips_when_disabled(disabled_settings):
    # No respx mock registered: a real request would error, proving none is made.
    assert search("企业所得税") == []


def test_search_skips_without_api_key(monkeypatch):
    settings = Settings(brave_search_enabled=True, brave_search_api_key="")
    monkeypatch.setattr(brave_search, "get_settings", lambda: settings)
    assert search("企业所得税") == []


def test_search_skips_trivial_query(enabled_settings):
    assert search("a") == []
    assert search("") == []


@respx.mock
def test_search_degrades_on_http_error(enabled_settings):
    respx.get(_ENDPOINT).mock(return_value=httpx.Response(500, text="boom"))
    assert search("企业所得税") == []


@respx.mock
def test_search_degrades_on_transport_error(enabled_settings):
    respx.get(_ENDPOINT).mock(side_effect=httpx.ConnectError("no route"))
    assert search("企业所得税") == []


def test_build_queries_numeric_article():
    queries = build_queries("企業所得稅法", "企業所得稅法#28", "小型微利企業減按25%計入")
    assert "企業所得稅法 第28條" in queries
    assert "企業所得稅法 修正 生效" in queries


def test_build_queries_deduplicates_and_skips_short_text():
    queries = build_queries("增值稅", "增值稅", "短")
    assert len(queries) == len(set(queries))
    assert all(len(q) >= 2 for q in queries)


def test_build_queries_non_numeric_article():
    queries = build_queries("財稅公告", "财税〔2026〕15号#1", "")
    assert any("财税〔2026〕15号" in q for q in queries)


@respx.mock
def test_gather_results_deduplicates_by_url(enabled_settings):
    respx.get(_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json=_payload(
                {"title": "same", "description": "d", "url": "https://example.gov.cn/same"},
            ),
        )
    )
    # Every query returns the same URL; it must collapse to one result.
    results = gather_results("企業所得稅法", "企業所得稅法#28", "小型微利企業減按25%計入")
    assert len(results) == 1
