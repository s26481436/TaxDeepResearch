"""Tests for the official 国家税务总局 policy-library search.

Validated against the live backend on 2026-08-13:
- Endpoint: GET https://www.chinatax.gov.cn/search5/search/s (same as crawler)
- A populated `searchWord` turns the crawler's enumeration into a lookup
- `searchWord` set to a 文號 returns exactly that document (total=1)
- `xxgk_aging` carries 全文有效 / 全文废止 / 已修改, or the string "null"
- Matched terms come back wrapped in <span> highlight markup
"""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest
import respx

from taxwatch.analysis import fgk_search
from taxwatch.analysis.fgk_search import _core_title, gather_results, search
from taxwatch.config import Settings

_ENDPOINT = "https://www.chinatax.gov.cn/search5/search/s"


@pytest.fixture
def live(monkeypatch):
    """Re-enable the search that conftest's guard switches off suite-wide.

    Every request is mocked with respx, so nothing leaves the machine.
    """
    settings = Settings(fgk_search_enabled=True, fgk_search_max_results=3, fgk_search_timeout=5)
    monkeypatch.setattr(fgk_search, "get_settings", lambda: settings)
    return settings


@pytest.fixture
def disabled(monkeypatch):
    settings = Settings(fgk_search_enabled=False)
    monkeypatch.setattr(fgk_search, "get_settings", lambda: settings)
    return settings


def _payload(*entries):
    return {"searchResultAll": {"searchTotal": list(entries), "total": len(entries)}}


def _entry(**over):
    base = {
        "title": "财政部 税务总局关于调整<span>增值税</span>税率的通知",
        "url": "http://fgk.chinatax.gov.cn/zcfgk/c100012/c5251620/content.html",
        "pubDate": "2018-04-04 00:00:00",
        "label": "财税文件",
        "xxgk_aging": "全文有效",
        "indexno": "财税〔2018〕32号",
        "content": "自2018年5月1日起，纳税人发生增值税应税销售行为...",
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# search()
# ---------------------------------------------------------------------------


@respx.mock
def test_strips_highlight_markup(live):
    respx.get(_ENDPOINT).mock(return_value=httpx.Response(200, json=_payload(_entry())))
    results = search("增值税 税率")

    assert len(results) == 1
    assert results[0].title == "财政部 税务总局关于调整增值税税率的通知"


@respx.mock
def test_maps_aging_and_metadata(live):
    respx.get(_ENDPOINT).mock(
        return_value=httpx.Response(200, json=_payload(_entry(xxgk_aging="全文废止")))
    )
    result = search("增值税")[0]

    assert result.aging == "全文废止"
    assert result.is_repealed
    assert result.document_number == "财税〔2018〕32号"
    assert result.effect_level == "财税文件"
    assert result.pub_date == "2018-04-04"
    # http:// must be upgraded, matching the crawler's behaviour.
    assert result.url.startswith("https://")


@respx.mock
def test_normalises_literal_null_aging(live):
    """The API returns the string "null", not JSON null, for unset 时效性."""
    respx.get(_ENDPOINT).mock(
        return_value=httpx.Response(200, json=_payload(_entry(xxgk_aging="null")))
    )
    assert search("增值税")[0].aging == ""


@respx.mock
def test_sends_the_search_word_and_date_window(live):
    route = respx.get(_ENDPOINT).mock(return_value=httpx.Response(200, json=_payload(_entry())))
    search("增值税", date_from="2026-01-01", date_to="2026-12-31")

    params = route.calls[0].request.url.params
    # The crawler leaves searchWord empty to enumerate; a lookup must fill it.
    assert params["searchWord"] == "增值税"
    assert params["cwrqStart"] == "2026-01-01"
    assert params["cwrqEnd"] == "2026-12-31"


@respx.mock
def test_drops_results_without_url(live):
    respx.get(_ENDPOINT).mock(
        return_value=httpx.Response(200, json=_payload(_entry(url=""), _entry()))
    )
    assert len(search("增值税")) == 1


def test_skips_when_disabled(disabled):
    # No respx mock registered: a real request would error, proving none is made.
    assert search("增值税") == []


def test_skips_trivial_query(live):
    assert search("") == []
    assert search("增") == []


@respx.mock
def test_degrades_on_http_error(live):
    respx.get(_ENDPOINT).mock(return_value=httpx.Response(500, text="boom"))
    assert search("增值税") == []


@respx.mock
def test_degrades_on_malformed_payload(live):
    respx.get(_ENDPOINT).mock(return_value=httpx.Response(200, json={"unexpected": True}))
    assert search("增值税") == []


# ---------------------------------------------------------------------------
# gather_results() — query strategy
# ---------------------------------------------------------------------------


@respx.mock
def test_wenhao_query_wins_and_stops(live):
    """A 文號 identifies a document outright, so nothing broader should follow."""
    route = respx.get(_ENDPOINT).mock(return_value=httpx.Response(200, json=_payload(_entry())))
    results = gather_results("财政部关于调整增值税税率的通知", "财税〔2018〕32号")

    assert len(results) == 1
    assert route.call_count == 1
    assert route.calls[0].request.url.params["searchWord"] == "财税〔2018〕32号"


@respx.mock
def test_falls_back_to_title_when_wenhao_misses(live):
    route = respx.get(_ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json=_payload()),  # 文號 finds nothing
            httpx.Response(200, json=_payload(_entry())),  # title does
        ]
    )
    results = gather_results("财政部 税务总局关于调整增值税税率的通知", "财税〔9999〕1号")

    assert len(results) == 1
    assert route.call_count == 2


@respx.mock
def test_applies_date_window_around_the_amendment(live):
    route = respx.get(_ENDPOINT).mock(return_value=httpx.Response(200, json=_payload(_entry())))
    gather_results("增值税法", "", datetime(2026, 6, 15))

    params = route.calls[0].request.url.params
    assert params["cwrqStart"] == "2026-03-17"  # 90 days before
    assert params["cwrqEnd"] == "2026-09-13"  # 90 days after


@respx.mock
def test_never_issues_an_article_level_query(live):
    """Article-level queries returned mirror sites and cost one request each."""
    route = respx.get(_ENDPOINT).mock(return_value=httpx.Response(200, json=_payload(_entry())))
    gather_results("增值税法", "")

    for call in route.calls:
        assert "第" not in call.request.url.params["searchWord"]


# ---------------------------------------------------------------------------
# _core_title()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title,expected",
    [
        ("国家税务总局关于电池消费税征收管理有关事项的公告", "电池消费税征收管理"),
        ("财政部 税务总局关于调整增值税税率的通知", "调整增值税税率"),
        ("国务院关于废止部分行政法规的决定", "废止部分行政法规"),
        # No 关于 wrapper and no suffix to strip — the title is already the core.
        ("中华人民共和国增值税法", "中华人民共和国增值税法"),
    ],
)
def test_core_title_strips_boilerplate(title, expected):
    assert _core_title(title) == expected


def test_core_title_keeps_original_when_stripping_leaves_nothing():
    assert _core_title("关于的公告") == "关于的公告"


def test_core_title_handles_empty():
    assert _core_title("") == ""
