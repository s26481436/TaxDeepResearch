"""Tests for reference-corpus import and lookup."""

from datetime import datetime

import pytest

from taxwatch.corpus import store
from taxwatch.corpus.loader import _build, _date, parse_tax_keys
from taxwatch.models import CorpusDocument

# ---------- tax label parsing ----------


def test_parse_tax_keys_single():
    assert parse_tax_keys("税收政策-增值税") == ["vat"]


def test_parse_tax_keys_multi_valued():
    """The corpus packs several tax types into one comma-separated field."""
    keys = parse_tax_keys("税收政策-增值税,税费征管")
    assert keys == ["vat", "collection"]


def test_parse_tax_keys_deduplicates():
    # 增值税 and 营业税 both map to vat; the key must not repeat.
    assert parse_tax_keys("税收政策-增值税,税收政策-营业税") == ["vat"]


def test_parse_tax_keys_drops_unknown_labels():
    keys = parse_tax_keys("税收政策-增值税,税收政策-某种没听过的税")
    assert keys == ["vat"]


def test_parse_tax_keys_empty():
    assert parse_tax_keys("") == []
    assert parse_tax_keys(None) == []


def test_parse_tax_keys_land_appreciation_is_property_not_vat():
    """土地增值税 contains 增值税 as a substring — it must not become vat."""
    assert parse_tax_keys("税收政策-土地增值税") == ["property"]


# ---------- row building ----------


def test_build_normalizes_document_number():
    doc = _build(
        {"title": "测试公告", "document_number": "财税[2026]15号"},
        "chinatax",
        "2026-02-27",
        "https://fgk.chinatax.gov.cn",
    )
    assert doc.document_number == "财税〔2026〕15号"


def test_build_falls_back_to_title_for_missing_document_number():
    """12.8% of corpus rows have no 文號 column value; the title usually
    still carries one."""
    doc = _build(
        {"title": "国家税务总局公告2026年第6号 关于某事项的公告", "document_number": ""},
        "chinatax",
        "2026-02-27",
        "https://fgk.chinatax.gov.cn",
    )
    assert doc.document_number == "国家税务总局公告2026年第6号"


def test_build_infers_tax_key_when_label_missing():
    """26% of rows have no tax_type; fall back to the title heuristic."""
    doc = _build(
        {"title": "中华人民共和国企业所得税法", "tax_type": ""},
        "chinatax",
        "",
        "https://fgk.chinatax.gov.cn",
    )
    assert doc.tax_keys == ["enterprise_income"]


def test_build_prefers_corpus_label_over_heuristic():
    doc = _build(
        {"title": "关于优化企业所得税预缴纳税申报有关事项的公告", "tax_type": "税费征管"},
        "chinatax",
        "",
        "https://fgk.chinatax.gov.cn",
    )
    # The heuristic would say enterprise_income; the corpus says 征管.
    assert doc.tax_keys == ["collection"]


def test_build_makes_urls_absolute():
    doc = _build(
        {"title": "x", "url": "/zcfgk/c100009/c5193032/content.html"},
        "chinatax",
        "",
        "https://fgk.chinatax.gov.cn",
    )
    assert doc.url == "https://fgk.chinatax.gov.cn/zcfgk/c100009/c5193032/content.html"


def test_build_leaves_absolute_urls_alone():
    doc = _build({"title": "x", "url": "https://other.gov.cn/a"}, "c", "", "https://b")
    assert doc.url == "https://other.gov.cn/a"


def test_build_requires_a_title():
    assert _build({"title": ""}, "c", "", "https://b") is None


def test_build_strips_bom_from_fields():
    doc = _build({"title": "﻿测试公告"}, "c", "", "https://b")
    assert doc.title == "测试公告"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-02-13", datetime(2026, 2, 13)),
        ("2026/02/13", datetime(2026, 2, 13)),
        ("2026年02月13日", datetime(2026, 2, 13)),
        ("", None),
        ("not a date", None),
    ],
)
def test_date_parsing(raw, expected):
    assert _date(raw) == expected


# ---------- store ----------


@pytest.fixture
def corpus(session):
    rows = [
        CorpusDocument(
            corpus_key="chinatax",
            corpus_version="2026-02-27",
            document_number="财税〔2026〕15号",
            title="关于制造业企业研发费用加计扣除政策的公告",
            channel="财税文件",
            effect_level="财税文件",
            tax_type_raw="税收政策-企业所得税",
            tax_keys=["enterprise_income"],
            aging="全文有效",
            written_date=datetime(2026, 1, 15),
            url="https://fgk.chinatax.gov.cn/a",
            content="制造业企业研发费用按实际发生额的100%在税前加计扣除。",
        ),
        CorpusDocument(
            corpus_key="chinatax",
            corpus_version="2026-02-27",
            document_number="国税发〔2003〕67号",
            title="关于旧政策的通知",
            channel="税务规范性文件",
            tax_type_raw="税收政策-增值税",
            tax_keys=["vat"],
            aging="全文废止",
            written_date=datetime(2003, 6, 1),
            url="https://fgk.chinatax.gov.cn/b",
            content="旧的增值税规定。",
        ),
        CorpusDocument(
            corpus_key="chinatax",
            document_number="",
            title="没有文号的文件",
            tax_keys=[],
            content="无关内容。",
        ),
    ]
    session.add_all(rows)
    session.commit()
    return rows


def test_lookup_by_wenhao(session, corpus):
    doc = store.lookup(session, "财税〔2026〕15号")
    assert doc is not None
    assert "研发费用" in doc.content


def test_lookup_tolerates_bracket_style(session, corpus):
    """A citation written with ASCII brackets must still find the row."""
    assert store.lookup(session, "财税[2026]15号") is not None
    assert store.lookup(session, "财税 〔2026〕 15 号") is not None


def test_lookup_misses_cleanly(session, corpus):
    assert store.lookup(session, "财税〔2099〕1号") is None
    assert store.lookup(session, "") is None


def test_lookup_many_batches(session, corpus):
    hits = store.lookup_many(
        session, ["财税〔2026〕15号", "国税发〔2003〕67号", "不存在〔2020〕1号"]
    )
    assert set(hits) == {"财税〔2026〕15号", "国税发〔2003〕67号"}


def test_search_matches_title_then_content(session, corpus):
    assert store.search(session, "研发费用")
    assert store.search(session, "旧的增值税规定")


def test_search_ignores_trivial_query(session, corpus):
    assert store.search(session, "x") == []


def test_repealed_document_numbers(session, corpus):
    repealed = store.repealed_document_numbers(session)
    assert repealed == {"国税发〔2003〕67号": "全文废止"}


def test_is_repealed_property(session, corpus):
    assert store.lookup(session, "国税发〔2003〕67号").is_repealed
    assert not store.lookup(session, "财税〔2026〕15号").is_repealed


def test_classify_document_prefers_corpus_label(session, corpus):
    """Title says 增值税, corpus label says vat — but for a document whose
    title would mislead, the label wins."""
    tax = store.classify_document(session, "关于旧政策的通知", "国税发〔2003〕67号")
    assert tax.key == "vat"


def test_classify_document_falls_back_to_heuristic(session, corpus):
    tax = store.classify_document(session, "中华人民共和国印花税法", "不在语料库〔2020〕1号")
    assert tax.key == "stamp"


def test_make_classifier_uses_index(session, corpus):
    classify = store.make_classifier(session)
    assert classify("关于旧政策的通知", "国税发〔2003〕67号").key == "vat"
    assert classify("中华人民共和国印花税法", "").key == "stamp"


def test_make_classifier_survives_empty_corpus(session):
    classify = store.make_classifier(session)
    assert classify("中华人民共和国企业所得税法", "").key == "enterprise_income"


def test_stats(session, corpus):
    rows = store.stats(session)
    assert len(rows) == 1
    assert rows[0]["documents"] == 3
    assert rows[0]["with_document_number"] == 2
    assert rows[0]["repealed"] == 1
