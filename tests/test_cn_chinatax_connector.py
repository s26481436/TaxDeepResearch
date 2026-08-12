"""Tests for the 国家税务总局 (fgk) connector.

Validated against the live backend on 2026-08-11:
- Endpoint: GET https://www.chinatax.gov.cn/search5/search/s
- Documents live under searchResultAll.searchTotal
- pubDate format: "YYYY-MM-DD HH:MM:SS", ordered newest first
- Article pages print the 文号 directly beneath the title
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from taxwatch.connectors.base import DocumentRef
from taxwatch.connectors.cn_chinatax import (
    CnChinataxConnector,
    _id_from_url,
    _parse_api_date,
    _wenhao_from_article,
)

# ---------------------------------------------------------------------------
# Fixtures — shaped like the live API response
# ---------------------------------------------------------------------------


def _entry(title: str, doc_id: str, pub_date: str, label: str = "税务规范性文件") -> dict:
    return {
        "title": title,
        "url": f"http://fgk.chinatax.gov.cn/zcfgk/c100012/{doc_id}/content.html",
        "pubDate": pub_date,
        "cwrq": pub_date,
        "label": label,
        "xxgk_effectLevel": label,
        "xxgk_aging": "尚未生效",
        "pubName": "国家税务总局",
        "indexno": "",
        "content": "摘要内容",
    }


def _api_response(entries: list[dict]) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"searchResultAll": {"searchTotal": entries, "total": len(entries)}}
    return resp


BATTERY_TITLE = "国家税务总局关于电池消费税征收管理有关事项的公告"

ARTICLE_HTML = """
<html><body>
  <div class="content">
    <h2>国家税务总局关于电池消费税征收管理有关事项的公告</h2>
    <p>国家税务总局公告2026年第16号</p>
    <p>成文日期：2026-07-31</p>
    <p>一、纳税人销售电池产品，应当选择"电池"类编码开具发票。</p>
    <p>依据财税〔2019〕99号的规定另有安排。</p>
  </div>
</body></html>
"""


@pytest.fixture
def connector():
    return CnChinataxConnector({"max_pages": 1})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestParseApiDate:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("2026-07-31 00:00:00", datetime(2026, 7, 31)),
            ("2026-07-31", datetime(2026, 7, 31)),
            ("", None),
            ("not a date", None),
        ],
    )
    def test_variants(self, raw, expected):
        assert _parse_api_date(raw) == expected


class TestIdFromUrl:
    def test_extracts_fgk_content_id(self):
        url = "https://fgk.chinatax.gov.cn/zcfgk/c100012/c5251620/content.html"
        assert _id_from_url(url) == "c5251620"

    def test_returns_none_when_absent(self):
        assert _id_from_url("https://example.com/") is None


class TestWenhaoFromArticle:
    def test_reads_number_under_title(self):
        assert _wenhao_from_article(ARTICLE_HTML.encode()) == "国家税务总局公告2026年第16号"

    def test_ignores_wenhao_cited_deep_in_body(self):
        """A 文号 quoted in the body must not be mistaken for the document's own."""
        html = (
            '<html><body><div class="content">'
            + "<p>标题</p>"
            + "<p>填充</p>" * 80
            + "<p>依据财税〔2019〕99号规定</p>"
            + "</div></body></html>"
        )
        assert _wenhao_from_article(html.encode()) == ""

    def test_returns_empty_without_content_block(self):
        assert _wenhao_from_article(b"<html><body><p>x</p></body></html>") == ""


# ---------------------------------------------------------------------------
# discover()
# ---------------------------------------------------------------------------


class TestDiscover:
    def test_maps_api_entries_to_refs(self, connector):
        entries = [
            _entry(BATTERY_TITLE, "c5251620", "2026-07-31 00:00:00"),
            _entry(
                "财政部 税务总局关于调整城镇土地使用税政策的公告", "c5251406", "2026-07-27 00:00:00"
            ),
        ]
        with patch("taxwatch.connectors.cn_chinatax.fetch_with_retry") as mock_fetch:
            mock_fetch.return_value = _api_response(entries)
            refs = connector.discover()

        assert len(refs) == 2
        first = refs[0]
        assert first.external_id == "c5251620"
        assert first.issued_at == datetime(2026, 7, 31)
        assert first.doc_type == "regulation"
        assert first.url.startswith("https://")  # http:// upgraded
        assert first.metadata["title"] == first.title
        assert first.metadata["effect_level"] == "税务规范性文件"

    def test_since_stops_at_older_document(self, connector):
        entries = [
            _entry("新公告", "c5250001", "2026-07-31 00:00:00"),
            _entry("旧公告", "c5250002", "2020-01-01 00:00:00"),
        ]
        with patch("taxwatch.connectors.cn_chinatax.fetch_with_retry") as mock_fetch:
            mock_fetch.return_value = _api_response(entries)
            refs = connector.discover(since=datetime(2026, 1, 1))

        assert [r.external_id for r in refs] == ["c5250001"]

    def test_deduplicates_across_pages(self):
        conn = CnChinataxConnector({"max_pages": 3})
        entries = [_entry("重复公告", "c5250001", "2026-07-31 00:00:00")]
        with patch("taxwatch.connectors.cn_chinatax.fetch_with_retry") as mock_fetch:
            mock_fetch.return_value = _api_response(entries)
            refs = conn.discover()

        assert len(refs) == 1

    def test_stops_on_empty_page(self):
        conn = CnChinataxConnector({"max_pages": 5})
        with patch("taxwatch.connectors.cn_chinatax.fetch_with_retry") as mock_fetch:
            mock_fetch.return_value = _api_response([])
            refs = conn.discover()

        assert refs == []
        # One call per label (6 default labels), each returns empty on page 0
        assert mock_fetch.call_count == 6

    def test_keyword_filter_is_opt_in(self):
        """No keywords configured means the API's column/label scoping stands."""
        entries = [_entry(BATTERY_TITLE, "c5250001", "2026-07-31 00:00:00")]
        with patch("taxwatch.connectors.cn_chinatax.fetch_with_retry") as mock_fetch:
            mock_fetch.return_value = _api_response(entries)
            assert len(CnChinataxConnector({"max_pages": 1}).discover()) == 1

            mock_fetch.return_value = _api_response(entries)
            filtered = CnChinataxConnector({"max_pages": 1, "keywords": ["增值税"]}).discover()
            assert filtered == []

    def test_survives_backend_error(self, connector):
        with patch("taxwatch.connectors.cn_chinatax.fetch_with_retry") as mock_fetch:
            mock_fetch.side_effect = Exception("backend down")
            assert connector.discover() == []


# ---------------------------------------------------------------------------
# fetch()
# ---------------------------------------------------------------------------


class TestFetch:
    def test_backfills_wenhao_from_article(self, connector):
        ref = DocumentRef(
            external_id="c5251620",
            title=BATTERY_TITLE,
            doc_type="regulation",
            url="https://fgk.chinatax.gov.cn/zcfgk/c100012/c5251620/content.html",
            metadata={"wenhao": ""},
        )
        resp = MagicMock(content=ARTICLE_HTML.encode(), headers={"content-type": "text/html"})
        with patch("taxwatch.connectors.cn_chinatax.fetch_with_retry", return_value=resp):
            raw = connector.fetch(ref)

        assert raw.metadata["wenhao"] == "国家税务总局公告2026年第16号"
        assert not raw.metadata.get("skip")

    def test_keeps_wenhao_supplied_by_api(self, connector):
        ref = DocumentRef(
            external_id="c5250001",
            title="公告",
            doc_type="regulation",
            url="https://fgk.chinatax.gov.cn/zcfgk/c100012/c5250001/content.html",
            metadata={"wenhao": "财税〔2026〕1号"},
        )
        resp = MagicMock(content=ARTICLE_HTML.encode(), headers={"content-type": "text/html"})
        with patch("taxwatch.connectors.cn_chinatax.fetch_with_retry", return_value=resp):
            raw = connector.fetch(ref)

        assert raw.metadata["wenhao"] == "财税〔2026〕1号"

    def test_404_marks_document_skippable(self, connector):
        import httpx

        ref = DocumentRef(
            external_id="c5250404",
            title="已下架",
            doc_type="regulation",
            url="https://fgk.chinatax.gov.cn/zcfgk/c100012/c5250404/content.html",
            metadata={},
        )
        error = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock(status_code=404)
        )
        with patch("taxwatch.connectors.cn_chinatax.fetch_with_retry", side_effect=error):
            raw = connector.fetch(ref)

        assert raw.metadata["skip"] is True
        assert raw.content == b""

    def test_non_404_errors_propagate(self, connector):
        import httpx

        ref = DocumentRef(
            external_id="c5250500",
            title="伺服器錯誤",
            doc_type="regulation",
            url="https://fgk.chinatax.gov.cn/zcfgk/c100012/c5250500/content.html",
            metadata={},
        )
        error = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock(status_code=500)
        )
        with patch("taxwatch.connectors.cn_chinatax.fetch_with_retry", side_effect=error):
            with pytest.raises(httpx.HTTPStatusError):
                connector.fetch(ref)
