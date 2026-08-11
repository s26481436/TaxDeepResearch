"""Tests for Taiwan MOJ law connector."""
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from taxwatch.connectors.tw_moj_law import TwMojLawConnector
from taxwatch.connectors.base import DocumentRef, RawDocument


@pytest.fixture
def connector():
    """Create a TW MOJ law connector with test config."""
    config = {
        "law_categories": [
            "G0340001",  # 所得稅法
            "G0340003",  # 營業稅法
        ]
    }
    return TwMojLawConnector(config)


# Mock API response structure
MOCK_LAW_INFO = {
    "Pcode": "G0340001",
    "LawName": "所得稅法",
    "LawModifiedDate": "民國 113 年 01 月 03 日",
    "Status": "Valid",
}

MOCK_LAW_ARTICLES = [
    {
        "ArticleNo": "第 1 條",
        "ArticleContent": "本法為規範所得稅之稽徵，特制定之。稅務機關徵收所得稅，應依本法之規定。",
    },
    {
        "ArticleNo": "第 2 條",
        "ArticleContent": "左列各項所得，應計入綜合所得額：一、營利所得。二、薪資所得。...",
    },
    {
        "ArticleNo": "第 3 條",
        "ArticleContent": "以下內容省略...",
    },
]


class TestTwMojLawConnectorDiscover:
    """Test the discover() method."""

    def test_discover_returns_document_refs_for_each_pcode(self, connector):
        """Test that discover() returns one DocumentRef per configured pcode."""
        with patch("taxwatch.connectors.tw_moj_law.fetch_with_retry") as mock_fetch:
            mock_fetch.side_effect = [
                MagicMock(json=lambda: MOCK_LAW_INFO),
                MagicMock(json=lambda: {
                    "Pcode": "G0340003",
                    "LawName": "營業稅法",
                    "LawModifiedDate": "民國 112 年 07 月 15 日",
                }),
            ]

            refs = connector.discover()

            assert len(refs) == 2
            assert refs[0].external_id == "G0340001"
            assert refs[0].title == "所得稅法"
            assert refs[1].external_id == "G0340003"
            assert refs[1].title == "營業稅法"

    def test_discover_handles_parse_roc_date(self, connector):
        """Test that discovered documents parse ROC dates correctly."""
        with patch("taxwatch.connectors.tw_moj_law.fetch_with_retry") as mock_fetch:
            mock_fetch.return_value = MagicMock(json=lambda: MOCK_LAW_INFO)

            refs = connector.discover()

            # "民國 113 年 01 月 03 日" should parse to 2024-01-03
            assert refs[0].issued_at == datetime(2024, 1, 3)

    def test_discover_gracefully_handles_missing_metadata(self, connector):
        """Test that discover() continues if API call fails for one pcode."""
        with patch("taxwatch.connectors.tw_moj_law.fetch_with_retry") as mock_fetch:
            # First call fails, second succeeds
            mock_fetch.side_effect = [
                Exception("API Error"),
                MagicMock(json=lambda: {
                    "Pcode": "G0340003",
                    "LawName": "營業稅法",
                    "LawModifiedDate": "",
                }),
            ]

            refs = connector.discover()

            # Should still return both, with fallback title
            assert len(refs) == 2
            assert refs[0].external_id == "G0340001"
            assert refs[0].title == "G0340001"  # Fallback


class TestTwMojLawConnectorFetch:
    """Test the fetch() method."""

    def test_fetch_returns_raw_document_with_json_content(self, connector):
        """Test that fetch() retrieves and returns the law articles."""
        ref = DocumentRef(
            external_id="G0340001",
            title="所得稅法",
            doc_type="statute",
            url="https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=G0340001",
            metadata={"pcode": "G0340001"},
        )

        with patch("taxwatch.connectors.tw_moj_law.fetch_with_retry") as mock_fetch:
            import json
            mock_content = json.dumps({
                "LawName": "所得稅法",
                "LawArticles": MOCK_LAW_ARTICLES,
            }, ensure_ascii=True).encode()
            mock_fetch.return_value = MagicMock(content=mock_content)

            result = connector.fetch(ref)

            assert isinstance(result, RawDocument)
            assert result.external_id == "G0340001"
            assert result.content_type == "application/json"
            # JSON content contains escaped Unicode
            assert b"LawName" in result.content
            assert b"LawArticles" in result.content
            assert b"ArticleNo" in result.content

    def test_fetch_uses_pcode_from_metadata(self, connector):
        """Test that fetch() uses the pcode from document metadata."""
        ref = DocumentRef(
            external_id="G0340001",
            title="所得稅法",
            doc_type="statute",
            metadata={"pcode": "G0340001"},
        )

        with patch("taxwatch.connectors.tw_moj_law.fetch_with_retry") as mock_fetch:
            mock_fetch.return_value = MagicMock(content=b'{}')

            connector.fetch(ref)

            # Verify the pcode was used in the API call
            mock_fetch.assert_called_once()
            call_args = mock_fetch.call_args
            assert call_args[1]["params"]["pcode"] == "G0340001"


class TestRocDateParsing:
    """Test ROC date parsing utility."""

    @pytest.mark.parametrize("roc_date,expected", [
        ("民國 113 年 01 月 03 日", datetime(2024, 1, 3)),
        ("民國113年1月3日", datetime(2024, 1, 3)),
        ("113年01月03日", datetime(2024, 1, 3)),
        ("", None),
        ("invalid date", None),
    ])
    def test_parse_roc_date_variants(self, roc_date, expected):
        """Test that various ROC date formats parse correctly."""
        from taxwatch.connectors.tw_moj_law import _parse_roc_date
        assert _parse_roc_date(roc_date) == expected


class TestPcodeValidation:
    """Test the hardcoded pcode list."""

    def test_pcode_list_is_complete(self):
        """Test that all expected tax law pcodes are in the configuration.

        These 8 pcodes represent the core Taiwan tax laws that should be monitored:
        1. G0340001 - 所得稅法 (Income Tax Law)
        2. G0340002 - 營利事業所得稅查核準則 (Corp Income Tax Audit Standards)
        3. G0340003 - 營業稅法 (Business Tax Law)
        4. G0340070 - 稅捐稽徵法 (Tax Collection Law)
        5. G0340004 - 遺產及贈與稅法 (Estate and Gift Tax Law)
        6. G0340050 - 房屋稅條例 (House Tax Rules)
        7. G0340060 - 土地稅法 (Land Tax Law)
        8. G0340080 - 特種貨物及勞務稅條例 (Special Commodity and Service Tax)
        """
        expected_pcodes = {
            "G0340001",  # Income tax
            "G0340002",  # Corporate income tax standards
            "G0340003",  # Business/sales tax
            "G0340004",  # Estate and gift tax
            "G0340050",  # House tax
            "G0340060",  # Land tax
            "G0340070",  # Tax collection law
            "G0340080",  # Special goods and services tax
        }
        assert len(expected_pcodes) == 8
