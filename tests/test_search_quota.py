"""Guards on what a run may spend at the metered search API.

The regression this file exists for: external corroboration used to be fetched
per changed article, with three query angles each. A statute amended in fifty
places therefore issued 150 requests — fifty of them byte-identical, because
one angle was built from the document title alone. On Brave's free tier
(2,000/month) a single amended document cost 7.5% of the month.

Corroboration describes the amendment, not the article. These tests hold that
line, and hold the three defences that bound whatever still reaches the API:
cache, per-run budget, and rate limit.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from taxwatch.analysis import brave_search, evidence, fgk_search
from taxwatch.analysis.brave_search import gather_results, search
from taxwatch.config import Settings
from taxwatch.models import Base

_BRAVE = "https://api.search.brave.com/res/v1/web/search"
_FGK = "https://www.chinatax.gov.cn/search5/search/s"


@pytest.fixture
def brave_on(monkeypatch):
    settings = Settings(
        brave_search_api_key="k",
        brave_search_enabled=True,
        brave_search_max_results=3,
        brave_search_max_queries_per_run=100,
        brave_search_min_interval=0.0,  # no real sleeping in tests
        brave_search_cache_ttl_days=30,
    )
    monkeypatch.setattr(brave_search, "get_settings", lambda: settings)
    return settings


def _brave_payload(url="https://example.gov.cn/a"):
    return {"web": {"results": [{"title": "t", "description": "d", "url": url}]}}


def _fgk_payload(*entries):
    return {"searchResultAll": {"searchTotal": list(entries), "total": len(entries)}}


# ---------------------------------------------------------------------------
# The regression: fan-out must not scale with the size of an amendment
# ---------------------------------------------------------------------------


@respx.mock
def test_fifty_changed_articles_cost_one_query(brave_on):
    """The headline guarantee. Before this change the count was 150."""
    route = respx.get(_BRAVE).mock(return_value=httpx.Response(200, json=_brave_payload()))

    document_title = "中华人民共和国企业所得税法"
    for article in range(1, 51):
        gather_results(document_title, f"企业所得税法#{article}", f"第{article}条的新內容")

    assert route.call_count == 1


@respx.mock
def test_official_hit_spends_no_metered_quota(brave_on, monkeypatch):
    """When the official library answers, the metered API is never touched."""
    monkeypatch.setattr(
        fgk_search,
        "get_settings",
        lambda: Settings(fgk_search_enabled=True, fgk_search_max_results=3),
    )
    respx.get(_FGK).mock(
        return_value=httpx.Response(
            200,
            json=_fgk_payload(
                {
                    "title": "财政部 税务总局关于调整增值税税率的通知",
                    "url": "https://fgk.chinatax.gov.cn/a/content.html",
                    "pubDate": "2026-04-04 00:00:00",
                    "xxgk_aging": "全文有效",
                    "label": "财税文件",
                }
            ),
        )
    )
    brave_route = respx.get(_BRAVE).mock(return_value=httpx.Response(200, json=_brave_payload()))

    items = evidence.gather_for_document("财政部 税务总局关于调整增值税税率的通知")

    assert [e.origin for e in items] == [evidence.OFFICIAL]
    assert brave_route.call_count == 0
    assert brave_search.get_budget().spent == 0


@respx.mock
def test_metered_search_runs_only_when_official_finds_nothing(brave_on, monkeypatch):
    monkeypatch.setattr(
        fgk_search,
        "get_settings",
        lambda: Settings(fgk_search_enabled=True),
    )
    respx.get(_FGK).mock(return_value=httpx.Response(200, json=_fgk_payload()))
    brave_route = respx.get(_BRAVE).mock(return_value=httpx.Response(200, json=_brave_payload()))

    items = evidence.gather_for_document("某個沒有官方紀錄的法規")

    assert [e.origin for e in items] == [evidence.SEARCH]
    assert brave_route.call_count == 1


# ---------------------------------------------------------------------------
# Defence 1 — cache
# ---------------------------------------------------------------------------


@pytest.fixture
def cache_session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        yield db
    finally:
        db.close()


@respx.mock
def test_repeated_query_is_served_from_cache(brave_on, cache_session):
    route = respx.get(_BRAVE).mock(return_value=httpx.Response(200, json=_brave_payload()))

    first = search("增值税法 修正 生效", session=cache_session)
    second = search("增值税法 修正 生效", session=cache_session)

    assert route.call_count == 1
    assert [r.url for r in first] == [r.url for r in second]


@respx.mock
def test_cache_hit_does_not_consume_budget(brave_on, cache_session):
    respx.get(_BRAVE).mock(return_value=httpx.Response(200, json=_brave_payload()))

    search("增值税法 修正 生效", session=cache_session)
    spent_after_first = brave_search.get_budget().spent
    search("增值税法 修正 生效", session=cache_session)

    assert brave_search.get_budget().spent == spent_after_first == 1


@respx.mock
def test_expired_cache_entry_is_refetched(brave_on, cache_session):
    """TTL is what stops a stale answer being reused run after run.

    Expiry only matters across runs — within one run the in-run memo answers
    first — so this models the next day's pipeline starting a fresh run.
    """
    from datetime import datetime, timedelta

    from taxwatch.models import SearchCache

    route = respx.get(_BRAVE).mock(return_value=httpx.Response(200, json=_brave_payload()))
    search("增值税法 修正 生效", session=cache_session)

    row = cache_session.query(SearchCache).one()
    row.fetched_at = datetime.utcnow() - timedelta(days=31)
    cache_session.flush()

    brave_search.start_run()
    search("增值税法 修正 生效", session=cache_session)
    assert route.call_count == 2


@respx.mock
def test_cache_survives_across_runs_within_ttl(brave_on, cache_session):
    """The daily re-crawl of an unchanged corpus must re-bill nothing."""
    route = respx.get(_BRAVE).mock(return_value=httpx.Response(200, json=_brave_payload()))

    for _ in range(5):  # five consecutive daily runs
        brave_search.start_run()
        search("增值税法 修正 生效", session=cache_session)

    assert route.call_count == 1


@respx.mock
def test_search_works_without_a_session(brave_on):
    """No session means no persistent cache, not an error."""
    respx.get(_BRAVE).mock(return_value=httpx.Response(200, json=_brave_payload()))
    assert len(search("增值税法 修正 生效")) == 1


@respx.mock
def test_identical_query_is_billed_once_even_without_a_session(brave_on):
    """The in-run memo covers callers that have no database to cache into."""
    route = respx.get(_BRAVE).mock(return_value=httpx.Response(200, json=_brave_payload()))

    for _ in range(10):
        search("增值税法 修正 生效")

    assert route.call_count == 1
    assert brave_search.get_budget().spent == 1


@respx.mock
def test_new_run_refetches_after_the_memo_clears(brave_on):
    route = respx.get(_BRAVE).mock(return_value=httpx.Response(200, json=_brave_payload()))

    search("增值税法 修正 生效")
    brave_search.start_run()
    search("增值税法 修正 生效")

    assert route.call_count == 2


# ---------------------------------------------------------------------------
# Defence 2 — per-run budget
# ---------------------------------------------------------------------------


@respx.mock
def test_budget_stops_further_queries(monkeypatch):
    settings = Settings(
        brave_search_api_key="k",
        brave_search_enabled=True,
        brave_search_max_queries_per_run=2,
        brave_search_min_interval=0.0,
    )
    monkeypatch.setattr(brave_search, "get_settings", lambda: settings)
    route = respx.get(_BRAVE).mock(return_value=httpx.Response(200, json=_brave_payload()))
    brave_search.start_run(2)

    assert search("查詢一") != []
    assert search("查詢二") != []
    # Third is refused before any request is made.
    assert search("查詢三") == []
    assert route.call_count == 2


def test_budget_exhaustion_is_not_an_error(monkeypatch):
    settings = Settings(
        brave_search_api_key="k", brave_search_enabled=True, brave_search_min_interval=0.0
    )
    monkeypatch.setattr(brave_search, "get_settings", lambda: settings)
    brave_search.start_run(0)

    # No respx mock: proving no request is attempted at all.
    assert search("任何查詢") == []
    assert brave_search.get_budget().exhausted


def test_start_run_resets_the_budget(monkeypatch):
    settings = Settings(brave_search_max_queries_per_run=5)
    monkeypatch.setattr(brave_search, "get_settings", lambda: settings)

    budget = brave_search.start_run()
    budget.take()
    assert brave_search.get_budget().spent == 1

    brave_search.start_run()
    assert brave_search.get_budget().spent == 0


# ---------------------------------------------------------------------------
# Defence 3 — rate limit
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Optional severity gate
# ---------------------------------------------------------------------------


def _change(severity):
    from taxwatch.models import Change, ChangeType

    return Change(
        document_id=1,
        node_key="增值税法#1",
        change_type=ChangeType.MODIFIED,
        diff_text="",
        severity=severity,
    )


def test_severity_gate_is_off_by_default(monkeypatch):
    """An unset floor must not change coverage."""
    from taxwatch.jobs.pipeline import _worth_metered_search
    from taxwatch.models import Severity

    monkeypatch.setattr(
        "taxwatch.jobs.pipeline.get_settings", lambda: Settings(brave_search_min_severity="")
    )
    assert _worth_metered_search([_change(Severity.MINOR)]) is True


def test_severity_gate_blocks_below_the_floor(monkeypatch):
    from taxwatch.jobs.pipeline import _worth_metered_search
    from taxwatch.models import Severity

    monkeypatch.setattr(
        "taxwatch.jobs.pipeline.get_settings", lambda: Settings(brave_search_min_severity="major")
    )
    assert _worth_metered_search([_change(Severity.MINOR)]) is False
    assert _worth_metered_search([_change(Severity.MAJOR)]) is True
    assert _worth_metered_search([_change(Severity.CRITICAL)]) is True
    # One qualifying change in the document is enough to justify the lookup.
    assert _worth_metered_search([_change(Severity.MINOR), _change(Severity.MAJOR)]) is True


def test_unknown_severity_floor_is_ignored(monkeypatch):
    """A typo in configuration must not silently switch corroboration off."""
    from taxwatch.jobs.pipeline import _worth_metered_search
    from taxwatch.models import Severity

    monkeypatch.setattr(
        "taxwatch.jobs.pipeline.get_settings",
        lambda: Settings(brave_search_min_severity="huge"),
    )
    assert _worth_metered_search([_change(Severity.MINOR)]) is True


@respx.mock
def test_disallowed_metered_search_returns_nothing_without_calling(brave_on, monkeypatch):
    monkeypatch.setattr(fgk_search, "get_settings", lambda: Settings(fgk_search_enabled=True))
    respx.get(_FGK).mock(return_value=httpx.Response(200, json=_fgk_payload()))
    brave_route = respx.get(_BRAVE).mock(return_value=httpx.Response(200, json=_brave_payload()))

    assert evidence.gather_for_document("某法規", allow_metered=False) == []
    assert brave_route.call_count == 0


@respx.mock
def test_calls_are_spaced_by_the_minimum_interval(monkeypatch):
    """The free tier allows one query a second; going faster earns 429s."""
    slept: list[float] = []
    monkeypatch.setattr(brave_search.time, "sleep", slept.append)
    settings = Settings(
        brave_search_api_key="k",
        brave_search_enabled=True,
        brave_search_min_interval=1.1,
        brave_search_max_queries_per_run=10,
    )
    monkeypatch.setattr(brave_search, "get_settings", lambda: settings)
    respx.get(_BRAVE).mock(return_value=httpx.Response(200, json=_brave_payload()))

    search("查詢一")
    search("查詢二")

    assert slept, "second call must wait for the rate limit"
    assert 0 < slept[0] <= 1.1
