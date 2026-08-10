"""Tests for normalizers."""
from pathlib import Path

from taxwatch.connectors.base import RawDocument
from taxwatch.normalize.tw_law_json import TwLawJsonNormalizer
from taxwatch.normalize.tw_ruling_html import TwRulingHtmlNormalizer

FIXTURES = Path(__file__).parent / "fixtures"


def test_tw_law_json_normalizer():
    raw_content = (FIXTURES / "tw_law_sample.json").read_bytes()
    raw = RawDocument(external_id="G0340001", content=raw_content, content_type="application/json")

    normalizer = TwLawJsonNormalizer()
    result = normalizer.normalize(raw)

    assert result.title == "所得稅法"
    assert len(result.provisions) == 3

    keys = [p.node_key for p in result.provisions]
    assert "所得稅法#1" in keys
    assert "所得稅法#2" in keys
    assert "所得稅法#14" in keys

    art14 = next(p for p in result.provisions if p.node_key == "所得稅法#14")
    assert "營利所得" in art14.text
    assert "薪資所得" in art14.text


def test_tw_ruling_html_normalizer():
    raw_content = (FIXTURES / "tw_ruling_sample.html").read_bytes()
    raw = RawDocument(
        external_id="台財稅字第10904512340號",
        content=raw_content,
        content_type="text/html",
    )

    normalizer = TwRulingHtmlNormalizer()
    result = normalizer.normalize(raw)

    assert "台財稅字第10904512340號" in result.title or len(result.provisions) > 0

    all_text = " ".join(p.text for p in result.provisions)
    assert "所得稅法" in all_text
    assert "租賃所得" in all_text
