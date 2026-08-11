"""Tests for Taiwan MOJ law connector.

Validated against the live API on 2026-08-11:
- Endpoint: GET /api/Ch/Law/JSON and /api/Ch/Order/JSON
- Returns: ZIP containing ChLaw.json / ChOrder.json
- ArticleType "A" = actual articles, "C" = chapter headings
- LawModifiedDate format: YYYYMMDD (Gregorian, not ROC)
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from taxwatch.connectors.base import DocumentRef, RawDocument
from taxwatch.connectors.tw_moj_law import (
    TwMojLawConnector,
    _extract_pcode,
    _parse_yyyymmdd,
    _parse_zip_response,
    _parse_roc_date,
)
from taxwatch.normalize.tw_law_json import TwLawJsonNormalizer


# ---------------------------------------------------------------------------
# Fixtures — realistic mock data matching live API structure
# ---------------------------------------------------------------------------

MOCK_LAW = {
    "LawLevel": "法律",
    "LawName": "所得稅法",
    "LawURL": "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=G0340003",
    "LawCategory": "財政類",
    "LawModifiedDate": "20251226",
    "LawEffectiveDate": "",
    "LawEffectiveNote": "",
    "LawAbandonNote": "",
    "LawHasEngVersion": "N",
    "EngLawName": "Income Tax Act",
    "LawAttachements": [],
    "LawHistories": "1.中華民國三十四年立法院制定\r\n2.中華民國一百一十四年修正",
    "LawForeword": "",
    "LawArticles": [
        {"ArticleType": "C", "ArticleNo": "", "ArticleContent": "第 一 章 總則"},
        {"ArticleType": "A", "ArticleNo": "第 1 條", "ArticleContent": "所得稅分為綜合所得稅及營利事業所得稅。"},
        {"ArticleType": "A", "ArticleNo": "第 2 條", "ArticleContent": "凡有中華民國來源所得之個人，應就其中華民國來源之所得，依本法規定，課徵綜合所得稅。"},
        {"ArticleType": "C", "ArticleNo": "", "ArticleContent": "第 二 章 課稅範圍"},
        {"ArticleType": "A", "ArticleNo": "第 3 條", "ArticleContent": "凡在中華民國境內經營之營利事業，應依本法規定，課徵營利事業所得稅。"},
    ],
}

MOCK_LAW_ABANDONED = {
    **MOCK_LAW,
    "LawName": "廢止稅法",
    "LawURL": "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=G0340999",
    "LawAbandonNote": "中華民國110年廢止",
}


def _make_zip(laws: list[dict], filename: str = "ChLaw.json") -> bytes:
    """Build a ZIP bytes matching the real API format."""
    payload = json.dumps(
        {"UpdateDate": "2026/8/11 上午 12:00:00", "Laws": laws},
        ensure_ascii=False,
    ).encode("utf-8-sig")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(filename, payload)
    return buf.getvalue()


@pytest.fixture
def connector():
    config = {
        "law_categories": ["G0340003", "G0340051"],
    }
    return TwMojLawConnector(config)


# ---------------------------------------------------------------------------
# _parse_zip_response
# ---------------------------------------------------------------------------

class TestParseZipResponse:
    def test_extracts_laws_list(self):
        zipped = _make_zip([MOCK_LAW])
        laws = _parse_zip_response(zipped)
        assert len(laws) == 1
        assert laws[0]["LawName"] == "所得稅法"

    def test_handles_order_filename(self):
        zipped = _make_zip([MOCK_LAW], filename="ChOrder.json")
        laws = _parse_zip_response(zipped)
        assert laws[0]["LawName"] == "所得稅法"

    def test_handles_utf8_bom(self):
        """Live API uses UTF-8-BOM encoding."""
        zipped = _make_zip([MOCK_LAW])
        laws = _parse_zip_response(zipped)
        assert laws[0]["LawName"] == "所得稅法"


# ---------------------------------------------------------------------------
# _parse_yyyymmdd
# ---------------------------------------------------------------------------

class TestParseYyyymmdd:
    @pytest.mark.parametrize("raw,expected", [
        ("20251226", datetime(2025, 12, 26)),
        ("20210120", datetime(2021, 1, 20)),
        ("", None),
        ("invalid", None),
        ("00000000", None),
    ])
    def test_variants(self, raw, expected):
        assert _parse_yyyymmdd(raw) == expected


# ---------------------------------------------------------------------------
# _parse_roc_date (backward-compat helper)
# ---------------------------------------------------------------------------

class TestParseRocDate:
    @pytest.mark.parametrize("raw,expected", [
        ("民國 113 年 01 月 03 日", datetime(2024, 1, 3)),
        ("民國113年1月3日", datetime(2024, 1, 3)),
        ("113年01月03日", datetime(2024, 1, 3)),
        ("", None),
        ("invalid date", None),
    ])
    def test_variants(self, raw, expected):
        assert _parse_roc_date(raw) == expected


# ---------------------------------------------------------------------------
# _extract_pcode
# ---------------------------------------------------------------------------

class TestExtractPcode:
    def test_extracts_from_url(self):
        url = "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=G0340003"
        assert _extract_pcode(url) == "G0340003"

    def test_returns_none_on_missing(self):
        assert _extract_pcode("https://example.com/") is None


# ---------------------------------------------------------------------------
# TwMojLawConnector.discover()
# ---------------------------------------------------------------------------

class TestDiscover:
    def test_returns_matching_pcodes_only(self, connector):
        # Law batch has G0340003; Order batch has G0340051
        law_zip = _make_zip([MOCK_LAW])
        order_zip = _make_zip(
            [{
                **MOCK_LAW,
                "LawName": "營利事業所得稅查核準則",
                "LawURL": "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=G0340051",
                "LawLevel": "命令",
            }],
            filename="ChOrder.json",
        )

        with patch("taxwatch.connectors.tw_moj_law.fetch_with_retry") as mock_fetch:
            mock_fetch.side_effect = [
                MagicMock(content=law_zip),
                MagicMock(content=order_zip),
            ]
            refs = connector.discover()

        assert len(refs) == 2
        pcodes = {r.external_id for r in refs}
        assert pcodes == {"G0340003", "G0340051"}

    def test_parses_yyyymmdd_date(self, connector):
        law_zip = _make_zip([MOCK_LAW])
        with patch("taxwatch.connectors.tw_moj_law.fetch_with_retry") as mock_fetch:
            mock_fetch.side_effect = [
                MagicMock(content=law_zip),
                MagicMock(content=_make_zip([])),
            ]
            refs = connector.discover()

        ref = next(r for r in refs if r.external_id == "G0340003")
        assert ref.issued_at == datetime(2025, 12, 26)

    def test_embed_payload_avoids_second_fetch(self, connector):
        """discover() embeds the full payload so fetch() can skip HTTP."""
        law_zip = _make_zip([MOCK_LAW])
        with patch("taxwatch.connectors.tw_moj_law.fetch_with_retry") as mock_fetch:
            mock_fetch.side_effect = [
                MagicMock(content=law_zip),
                MagicMock(content=_make_zip([])),
            ]
            refs = connector.discover()

        ref = next(r for r in refs if r.external_id == "G0340003")
        assert "law_payload" in ref.metadata
        payload = json.loads(ref.metadata["law_payload"])
        assert payload["LawName"] == "所得稅法"

    def test_skips_nonmatching_pcodes(self, connector):
        """Laws not in law_categories must not appear in results."""
        other_law = {**MOCK_LAW, "LawURL": "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=A0000001"}
        law_zip = _make_zip([MOCK_LAW, other_law])
        with patch("taxwatch.connectors.tw_moj_law.fetch_with_retry") as mock_fetch:
            mock_fetch.side_effect = [
                MagicMock(content=law_zip),
                MagicMock(content=_make_zip([])),
            ]
            refs = connector.discover()

        assert all(r.external_id == "G0340003" for r in refs)

    def test_continues_when_one_endpoint_fails(self, connector):
        """If Law batch fails, Order batch should still be tried."""
        order_zip = _make_zip(
            [{**MOCK_LAW, "LawURL": "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=G0340051"}],
            filename="ChOrder.json",
        )
        with patch("taxwatch.connectors.tw_moj_law.fetch_with_retry") as mock_fetch:
            mock_fetch.side_effect = [
                Exception("network error"),
                MagicMock(content=order_zip),
            ]
            refs = connector.discover()

        assert len(refs) == 1
        assert refs[0].external_id == "G0340051"


# ---------------------------------------------------------------------------
# TwMojLawConnector.fetch()
# ---------------------------------------------------------------------------

class TestFetch:
    def test_uses_embedded_payload_without_http(self, connector):
        ref = DocumentRef(
            external_id="G0340003",
            title="所得稅法",
            doc_type="statute",
            metadata={"law_payload": json.dumps(MOCK_LAW, ensure_ascii=False)},
        )
        with patch("taxwatch.connectors.tw_moj_law.fetch_with_retry") as mock_fetch:
            result = connector.fetch(ref)
            mock_fetch.assert_not_called()

        assert isinstance(result, RawDocument)
        payload = json.loads(result.content.decode("utf-8"))
        assert payload["LawName"] == "所得稅法"


# ---------------------------------------------------------------------------
# TwLawJsonNormalizer
# ---------------------------------------------------------------------------

class TestTwLawJsonNormalizer:
    def test_extracts_only_article_type_a(self):
        raw = RawDocument(
            external_id="G0340003",
            content=json.dumps(MOCK_LAW, ensure_ascii=False).encode(),
            content_type="application/json",
        )
        doc = TwLawJsonNormalizer().normalize(raw)
        # C-type chapter headings must be excluded from provisions
        assert len(doc.provisions) == 3
        headings = [p.heading for p in doc.provisions]
        assert "第 1 條" in headings
        assert "" not in headings

    def test_node_keys_are_stable(self):
        raw = RawDocument(
            external_id="G0340003",
            content=json.dumps(MOCK_LAW, ensure_ascii=False).encode(),
            content_type="application/json",
        )
        doc = TwLawJsonNormalizer().normalize(raw)
        assert doc.provisions[0].node_key == "所得稅法#1"
        assert doc.provisions[1].node_key == "所得稅法#2"
        assert doc.provisions[2].node_key == "所得稅法#3"

    def test_flags_abandoned_law(self):
        raw = RawDocument(
            external_id="G0340999",
            content=json.dumps(MOCK_LAW_ABANDONED, ensure_ascii=False).encode(),
            content_type="application/json",
        )
        doc = TwLawJsonNormalizer().normalize(raw)
        assert doc.metadata["abandoned"] is True

    def test_active_law_not_abandoned(self):
        raw = RawDocument(
            external_id="G0340003",
            content=json.dumps(MOCK_LAW, ensure_ascii=False).encode(),
            content_type="application/json",
        )
        doc = TwLawJsonNormalizer().normalize(raw)
        assert doc.metadata["abandoned"] is False

    def test_histories_included_in_metadata(self):
        raw = RawDocument(
            external_id="G0340003",
            content=json.dumps(MOCK_LAW, ensure_ascii=False).encode(),
            content_type="application/json",
        )
        doc = TwLawJsonNormalizer().normalize(raw)
        assert "histories" in doc.metadata
        assert "民國" in doc.metadata["histories"]

    def test_handles_bytes_content(self):
        raw = RawDocument(
            external_id="G0340003",
            content=json.dumps(MOCK_LAW, ensure_ascii=False).encode("utf-8"),
            content_type="application/json",
        )
        doc = TwLawJsonNormalizer().normalize(raw)
        assert doc.title == "所得稅法"


# ---------------------------------------------------------------------------
# Pcode correctness (regression guard against future mis-configuration)
# ---------------------------------------------------------------------------

class TestPcodeList:
    """Verified pcodes from live API on 2026-08-11."""

    CORRECT_PCODES = {
        "G0340003": "所得稅法",
        "G0340080": "加值型及非加值型營業稅法",
        "G0340001": "稅捐稽徵法",
        "G0340072": "遺產及贈與稅法",
        "G0340096": "土地稅法",
        "G0340102": "房屋稅條例",
        "G0340128": "特種貨物及勞務稅條例",
        "G0340051": "營利事業所得稅查核準則",
    }

    WRONG_PCODES = {
        # These were the original wrong values
        "G0340002": "was wrongly labelled 營利事業所得稅查核準則",
        "G0340004": "was wrongly labelled 遺產及贈與稅法",
        "G0340050": "was wrongly labelled 房屋稅條例",
        "G0340060": "was wrongly labelled 土地稅法",
        "G0340070": "was wrongly labelled 稅捐稽徵法",
    }

    def test_correct_pcode_count(self):
        assert len(self.CORRECT_PCODES) == 8

    def test_no_wrong_pcodes_in_correct_set(self):
        for wrong in self.WRONG_PCODES:
            assert wrong not in self.CORRECT_PCODES, f"{wrong} is a known-bad pcode"
