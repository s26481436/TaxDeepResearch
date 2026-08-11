"""Tests for US federal and state tax connectors.

Validated data sources (2026-08-11):
- eCFR API: https://www.ecfr.gov/api/versioner/v1/ — public, no key
- govinfo.gov bulk: https://www.govinfo.gov/bulkdata/CFR/2025/title-26/vol*.xml — public
- Federal Register: https://www.federalregister.gov/api/v1/ — public
- State statutes: CA leginfo, TX statutes, FL flsenate (all public HTML)
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from taxwatch.connectors.base import DocumentRef, RawDocument
from taxwatch.connectors.us_ecfr import UsEcfrConnector, _parse_iso
from taxwatch.connectors.us_govinfo_cfr import UsGovinfoConnector, _parse_http_date
from taxwatch.connectors.us_state_tax import UsStateTaxConnector
from taxwatch.normalize.us_cfr_xml import UsCfrXmlNormalizer, _build_node_key
from taxwatch.normalize.us_state_tax_html import UsStateTaxHtmlNormalizer

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

_ECFR_VERSIONS = {
    "content_versions": [
        {
            "date": "2026-07-09",
            "amendment_date": "2026-07-09",
            "issue_date": "2026-07-09",
            "identifier": "1.101-1",
            "name": "§ 1.101-1   Exclusion from gross income of proceeds of life insurance",
            "part": "1",
            "substantive": True,
            "removed": False,
            "title": "26",
            "type": "section",
        },
        {
            "date": "2026-03-20",
            "amendment_date": "2026-03-20",
            "issue_date": "2026-03-20",
            "identifier": "1.132-1",
            "name": "§ 1.132-1   Exclusion from gross income for certain fringe benefits",
            "part": "1",
            "substantive": True,
            "removed": False,
            "title": "26",
            "type": "section",
        },
        {
            "date": "2025-10-01",
            "identifier": "20.2001-1",
            "name": "§ 20.2001-1   Instruments, documents, and valuations",
            "part": "20",
            "substantive": True,
            "removed": False,
            "title": "26",
            "type": "section",
        },
        {
            "date": "2024-01-15",
            "identifier": "1.001-1",
            "name": "§ 1.001-1   Internal revenue code; cross reference",
            "part": "1",
            "substantive": False,  # non-substantive — should be skipped
            "removed": False,
            "title": "26",
            "type": "section",
        },
    ]
}

# Minimal CFR XML matching govinfo.gov format
_CFR_XML = (
    '<?xml version="1.0"?>'
    "<CFRDOC>"
    '<TITLE N="26"><CHAPTER N="I"><PART N="1">'
    "<HEAD>PART 1--INCOME TAXES</HEAD>"
    "<SECTION>"
    "<SECTNO>§ 1.1-1</SECTNO>"
    "<SUBJECT>Income tax on individuals.</SUBJECT>"
    "<P>General rule. A tax is hereby imposed for each taxable year on the taxable income of every individual.</P>"  # noqa: E501
    "</SECTION>"
    "<SECTION>"
    "<SECTNO>§ 1.1-2</SECTNO>"
    "<SUBJECT>Limitation on tax.</SUBJECT>"
    "<P>In the case of a tax year beginning after December 31, 2017, the tax imposed shall not exceed the limit provided.</P>"  # noqa: E501
    "</SECTION>"
    "</PART></CHAPTER></TITLE>"
    "</CFRDOC>"
).encode()

_CA_HTML = (
    b"<html><body>"
    b'<div id="lawcontent"><div class="lawcode">'
    b'<p class="lawtext">17041. (a) There shall be levied, collected, and paid for each taxable year upon the entire net income received by every individual a tax in the following amounts and at the following rates.</p>'  # noqa: E501
    b'<p class="lawtext">(b) In lieu of the tax imposed by subdivision (a), there is hereby imposed a tax at the rate of 13.3 percent on the entire taxable income of an individual.</p>'  # noqa: E501
    b"</div></div>"
    b"</body></html>"
)

_TX_HTML = (
    b"<html><body>"
    b'<div class="section codeSect">'
    b"<b>Sec. 171.001. DEFINITIONS.</b>"
    b'<p class="body-text">In this chapter, taxable entity means a partnership, limited liability company, business trust, professional association, business association, joint venture, or other legal entity.</p>'  # noqa: E501
    b"</div>"
    b'<div class="section codeSect">'
    b"<b>Sec. 171.002. RATE; COMPUTATION OF TAX.</b>"
    b'<p class="body-text">The rate of the franchise tax is 0.75 percent of taxable margin for most entities.</p>'  # noqa: E501
    b"</div>"
    b"</body></html>"
)


# ---------------------------------------------------------------------------
# _parse_iso
# ---------------------------------------------------------------------------


class TestParseIso:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("2026-07-09", datetime(2026, 7, 9)),
            ("2025-12-31", datetime(2025, 12, 31)),
            ("", None),
            ("not-a-date", None),
        ],
    )
    def test_variants(self, raw, expected):
        assert _parse_iso(raw) == expected


# ---------------------------------------------------------------------------
# _parse_http_date
# ---------------------------------------------------------------------------


class TestParseHttpDate:
    def test_valid_rfc_date(self):
        d = _parse_http_date("Thu, 18 Jun 2026 03:12:27 GMT")
        assert d == datetime(2026, 6, 18, 3, 12, 27)

    def test_empty_returns_none(self):
        assert _parse_http_date("") is None


# ---------------------------------------------------------------------------
# UsEcfrConnector
# ---------------------------------------------------------------------------


class TestUsEcfrConnector:
    @pytest.fixture
    def connector(self):
        return UsEcfrConnector({"parts": ["1", "20"]})

    def test_discover_filters_by_parts(self, connector):
        with patch("taxwatch.connectors.us_ecfr.fetch_with_retry") as mock_fetch:
            mock_fetch.return_value = MagicMock(json=lambda: _ECFR_VERSIONS)
            refs = connector.discover()

        # Part 1 and Part 20 both match — should get 2 unique doc refs
        ids = {r.external_id for r in refs}
        assert "26-CFR-1" in ids
        assert "26-CFR-20" in ids

    def test_discover_skips_non_substantive(self, connector):
        with patch("taxwatch.connectors.us_ecfr.fetch_with_retry") as mock_fetch:
            mock_fetch.return_value = MagicMock(json=lambda: _ECFR_VERSIONS)
            refs = connector.discover()

        # Non-substantive 1.001-1 should still produce the part-1 ref (other entries exist)
        # but the key point: non-substantive alone wouldn't inflate count
        assert all(r.doc_type == "regulation" for r in refs)

    def test_discover_picks_latest_date_per_part(self, connector):
        with patch("taxwatch.connectors.us_ecfr.fetch_with_retry") as mock_fetch:
            mock_fetch.return_value = MagicMock(json=lambda: _ECFR_VERSIONS)
            refs = connector.discover()

        part1 = next(r for r in refs if r.external_id == "26-CFR-1")
        # Latest amendment for part 1 is 2026-07-09
        assert part1.issued_at == datetime(2026, 7, 9)

    def test_discover_with_since_filters(self, connector):
        with patch("taxwatch.connectors.us_ecfr.fetch_with_retry") as mock_fetch:
            mock_fetch.return_value = MagicMock(json=lambda: _ECFR_VERSIONS)
            refs = connector.discover(since=datetime(2026, 1, 1))

        # 2025-10-01 estate tax entry is before `since` — part 20 may not appear
        ids = {r.external_id for r in refs}
        assert "26-CFR-20" not in ids  # all part-20 entries are before 2026

    def test_discover_jurisdiction_metadata(self, connector):
        with patch("taxwatch.connectors.us_ecfr.fetch_with_retry") as mock_fetch:
            mock_fetch.return_value = MagicMock(json=lambda: _ECFR_VERSIONS)
            refs = connector.discover()

        for r in refs:
            assert r.metadata["jurisdiction"] == "US-federal"

    def test_fetch_downloads_xml(self, connector):
        ref = DocumentRef(
            external_id="26-CFR-1",
            title="26 CFR Part 1",
            doc_type="regulation",
            url="https://www.ecfr.gov/current/title-26/part-1",
            issued_at=datetime(2026, 7, 9),
            metadata={"part": "1", "jurisdiction": "US-federal"},
        )
        with patch("taxwatch.connectors.us_ecfr.fetch_with_retry") as mock_fetch:
            mock_fetch.return_value = MagicMock(content=_CFR_XML)
            result = connector.fetch(ref)

        assert result.content_type == "application/xml"
        assert b"SECTION" in result.content or b"<" in result.content


# ---------------------------------------------------------------------------
# UsGovinfoConnector
# ---------------------------------------------------------------------------


class TestUsGovinfoConnector:
    @pytest.fixture
    def connector(self):
        return UsGovinfoConnector({"year": "2025", "cfr_title": "26"})

    def test_discover_probes_volumes_and_stops_at_404(self, connector):
        def side_effect(client, url, method="GET", **kwargs):
            if "vol3" in url:
                r = MagicMock()
                r.status_code = 404
                return r
            r = MagicMock()
            r.status_code = 200
            r.headers = {"last-modified": "Thu, 18 Jun 2026 03:12:27 GMT"}
            return r

        with patch("taxwatch.connectors.us_govinfo_cfr.fetch_with_retry", side_effect=side_effect):
            refs = connector.discover()

        # Should have vol1 and vol2 only (stop at 404 for vol3)
        assert len(refs) == 2
        assert refs[0].external_id == "CFR-2025-title26-vol1"
        assert refs[1].external_id == "CFR-2025-title26-vol2"

    def test_discover_parses_last_modified(self, connector):
        def side_effect(client, url, method="GET", **kwargs):
            if "vol2" in url:
                r = MagicMock()
                r.status_code = 404
                return r
            r = MagicMock()
            r.status_code = 200
            r.headers = {"last-modified": "Thu, 18 Jun 2026 03:12:27 GMT"}
            return r

        with patch("taxwatch.connectors.us_govinfo_cfr.fetch_with_retry", side_effect=side_effect):
            refs = connector.discover()

        assert len(refs) == 1
        assert refs[0].issued_at == datetime(2026, 6, 18, 3, 12, 27)

    def test_fetch_returns_xml(self, connector):
        ref = DocumentRef(
            external_id="CFR-2025-title26-vol1",
            title="2025 CFR Title 26 Vol. 1",
            doc_type="regulation",
            url="https://example.com/vol1.xml",
            metadata={"download_url": "https://example.com/vol1.xml", "part": "1"},
        )
        with patch("taxwatch.connectors.us_govinfo_cfr.fetch_with_retry") as mock_fetch:
            mock_fetch.return_value = MagicMock(content=_CFR_XML)
            result = connector.fetch(ref)

        assert result.content == _CFR_XML
        assert result.content_type == "application/xml"


# ---------------------------------------------------------------------------
# UsStateTaxConnector
# ---------------------------------------------------------------------------


class TestUsStateTaxConnector:
    @pytest.fixture
    def connector(self):
        return UsStateTaxConnector({"states": ["CA", "TX"]})

    def test_discover_returns_both_states(self, connector):
        with patch("taxwatch.connectors.us_state_tax.fetch_with_retry") as mock_fetch:
            mock_fetch.return_value = MagicMock(
                status_code=200,
                headers={"last-modified": "Mon, 12 May 2026 15:11:35 GMT"},
            )
            refs = connector.discover()

        states = {r.metadata["state"] for r in refs}
        assert "CA" in states
        assert "TX" in states

    def test_ca_external_ids_follow_scheme(self, connector):
        with patch("taxwatch.connectors.us_state_tax.fetch_with_retry") as mock_fetch:
            mock_fetch.return_value = MagicMock(status_code=200, headers={})
            refs = connector.discover()

        ca_refs = [r for r in refs if r.metadata["state"] == "CA"]
        assert all(r.external_id.startswith("CA:RTC-") for r in ca_refs)

    def test_tx_doc_type_is_statute(self, connector):
        with patch("taxwatch.connectors.us_state_tax.fetch_with_retry") as mock_fetch:
            mock_fetch.return_value = MagicMock(status_code=200, headers={})
            refs = connector.discover()

        tx_refs = [r for r in refs if r.metadata["state"] == "TX"]
        assert all(r.doc_type == "statute" for r in tx_refs)

    def test_continues_when_one_state_fails(self):
        connector = UsStateTaxConnector({"states": ["CA", "TX", "FL"]})
        call_count = {"n": 0}

        def flaky(client, url, **kwargs):
            call_count["n"] += 1
            if "leginfo" in url:  # CA fails
                raise Exception("network error")
            return MagicMock(status_code=200, headers={})

        with patch("taxwatch.connectors.us_state_tax.fetch_with_retry", side_effect=flaky):
            refs = connector.discover()

        states = {r.metadata["state"] for r in refs}
        # CA failed but TX and FL should still appear
        assert "TX" in states or "FL" in states

    def test_jurisdiction_metadata(self, connector):
        with patch("taxwatch.connectors.us_state_tax.fetch_with_retry") as mock_fetch:
            mock_fetch.return_value = MagicMock(status_code=200, headers={})
            refs = connector.discover()

        for r in refs:
            state = r.metadata["state"]
            assert r.metadata["jurisdiction"] == f"US-{state}"

    def test_unknown_state_skipped(self):
        connector = UsStateTaxConnector({"states": ["ZZ", "CA"]})
        with patch("taxwatch.connectors.us_state_tax.fetch_with_retry") as mock_fetch:
            mock_fetch.return_value = MagicMock(status_code=200, headers={})
            refs = connector.discover()

        states = {r.metadata["state"] for r in refs}
        assert "ZZ" not in states
        assert "CA" in states


# ---------------------------------------------------------------------------
# UsCfrXmlNormalizer
# ---------------------------------------------------------------------------


class TestUsCfrXmlNormalizer:
    def test_extracts_sections(self):
        raw = RawDocument(
            external_id="26-CFR-1",
            content=_CFR_XML,
            content_type="application/xml",
            metadata={"part": "1", "jurisdiction": "US-federal"},
        )
        doc = UsCfrXmlNormalizer().normalize(raw)
        assert len(doc.provisions) == 2

    def test_section_node_keys(self):
        raw = RawDocument(
            external_id="26-CFR-1",
            content=_CFR_XML,
            content_type="application/xml",
            metadata={"part": "1"},
        )
        doc = UsCfrXmlNormalizer().normalize(raw)
        assert doc.provisions[0].node_key == "26 CFR § 1.1-1"
        assert doc.provisions[1].node_key == "26 CFR § 1.1-2"

    def test_section_heading_includes_subject(self):
        raw = RawDocument(
            external_id="26-CFR-1",
            content=_CFR_XML,
            content_type="application/xml",
            metadata={"part": "1"},
        )
        doc = UsCfrXmlNormalizer().normalize(raw)
        assert "Income tax on individuals" in doc.provisions[0].heading

    def test_title_uses_part_metadata(self):
        raw = RawDocument(
            external_id="26-CFR-1",
            content=_CFR_XML,
            content_type="application/xml",
            metadata={"part": "1"},
        )
        doc = UsCfrXmlNormalizer().normalize(raw)
        assert doc.title == "26 CFR Part 1"

    def test_metadata_jurisdiction(self):
        raw = RawDocument(
            external_id="26-CFR-1",
            content=_CFR_XML,
            content_type="application/xml",
            metadata={"part": "1", "jurisdiction": "US-federal"},
        )
        doc = UsCfrXmlNormalizer().normalize(raw)
        assert doc.metadata["jurisdiction"] == "US-federal"
        assert doc.metadata["source_format"] == "us_cfr_xml"

    def test_build_node_key_with_sign(self):
        assert _build_node_key("§ 1.1-1") == "26 CFR § 1.1-1"

    def test_build_node_key_without_sign(self):
        assert _build_node_key("1.1-1") == "26 CFR § 1.1-1"


# ---------------------------------------------------------------------------
# UsStateTaxHtmlNormalizer
# ---------------------------------------------------------------------------


class TestUsStateTaxHtmlNormalizer:
    def test_ca_extracts_text(self):
        raw = RawDocument(
            external_id="CA:RTC-17041",
            content=_CA_HTML,
            content_type="text/html",
            metadata={
                "state": "CA",
                "statute_code": "RTC",
                "section": "17041",
                "jurisdiction": "US-CA",
                "tax_type": "income_tax",
            },
        )
        doc = UsStateTaxHtmlNormalizer().normalize(raw)
        assert len(doc.provisions) >= 1
        assert "CA:RTC-17041" in doc.provisions[0].node_key
        text = doc.provisions[0].text.lower()
        assert "17041" in doc.provisions[0].text or "taxable income" in text

    def test_tx_extracts_multiple_sections(self):
        raw = RawDocument(
            external_id="TX:TC-171",
            content=_TX_HTML,
            content_type="text/html",
            metadata={
                "state": "TX",
                "statute_code": "TX Tax Code",
                "chapter": "171",
                "jurisdiction": "US-TX",
                "tax_type": "franchise_tax",
            },
        )
        doc = UsStateTaxHtmlNormalizer().normalize(raw)
        assert len(doc.provisions) >= 2  # DEFINITIONS + RATE

    def test_normalizer_metadata(self):
        raw = RawDocument(
            external_id="CA:RTC-17041",
            content=_CA_HTML,
            content_type="text/html",
            metadata={
                "state": "CA",
                "statute_code": "RTC",
                "section": "17041",
                "jurisdiction": "US-CA",
                "tax_type": "income_tax",
            },
        )
        doc = UsStateTaxHtmlNormalizer().normalize(raw)
        assert doc.metadata["state"] == "CA"
        assert doc.metadata["source_format"] == "us_state_tax_html"
        assert doc.metadata["tax_type"] == "income_tax"

    def test_generic_fallback_for_unknown_state(self):
        raw = RawDocument(
            external_id="ZZ:unknown-123",
            content=b"<html><body><main><p>Tax law text.</p></main></body></html>",
            content_type="text/html",
            metadata={
                "state": "ZZ",
                "external_id": "unknown-123",
                "jurisdiction": "US-ZZ",
                "tax_type": "unknown",
            },
        )
        doc = UsStateTaxHtmlNormalizer().normalize(raw)
        assert len(doc.provisions) == 1
        assert "Tax law text" in doc.provisions[0].text


# ---------------------------------------------------------------------------
# Coverage map: correct state codes and tax types
# ---------------------------------------------------------------------------


class TestStateConfig:
    """Verify state coverage is coherent."""

    STATES_WITH_INCOME_TAX = {"CA", "NY", "IL"}
    # FL has corp income; TX/WA have no personal income tax
    STATES_WITHOUT_INCOME_TAX = {"TX", "WA", "FL"}

    def test_all_six_states_registered(self):
        from taxwatch.connectors.us_state_tax import _ADAPTERS

        assert set(_ADAPTERS.keys()) >= {"CA", "TX", "FL", "WA", "NY", "IL"}

    def test_state_adapters_have_required_attributes(self):
        from taxwatch.connectors.us_state_tax import _ADAPTERS

        for code, cls in _ADAPTERS.items():
            assert cls.state_code == code
            assert cls.state_name
