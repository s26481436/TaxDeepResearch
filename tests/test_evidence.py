"""Tests for the evidence layer: corpus first, search second."""
from datetime import datetime

import httpx
import pytest
import respx

from taxwatch.analysis import brave_search, evidence
from taxwatch.analysis.evidence import CORPUS, SEARCH, Evidence, format_evidence, gather
from taxwatch.config import Settings
from taxwatch.models import CorpusDocument

_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


@pytest.fixture
def brave_on(monkeypatch):
    settings = Settings(brave_search_api_key="k", brave_search_enabled=True)
    monkeypatch.setattr(brave_search, "get_settings", lambda: settings)


@pytest.fixture
def brave_off(monkeypatch):
    settings = Settings(brave_search_enabled=False)
    monkeypatch.setattr(brave_search, "get_settings", lambda: settings)


@pytest.fixture
def corpus(session):
    session.add(CorpusDocument(
        corpus_key="chinatax", corpus_version="2026-02-27",
        document_number="财税〔2026〕15号",
        title="关于制造业企业研发费用加计扣除政策的公告",
        tax_keys=["enterprise_income"], aging="全文有效",
        written_date=datetime(2026, 1, 15), url="https://fgk.chinatax.gov.cn/a",
        content="制造业企业研发费用按实际发生额的100%在税前加计扣除。",
    ))
    session.add(CorpusDocument(
        corpus_key="chinatax", corpus_version="2026-02-27",
        document_number="国税发〔2003〕67号", title="已废止的旧通知",
        tax_keys=["vat"], aging="全文废止", url="https://fgk.chinatax.gov.cn/b",
        content="旧的规定。",
    ))
    session.commit()


def _brave_payload(*items):
    return {"web": {"results": list(items)}}


# ---------- gathering ----------

def test_corpus_hit_skips_the_search(session, corpus, brave_on):
    """The whole point: a resolvable citation must not reach the network.
    No respx mock is registered, so any HTTP call would raise."""
    items = gather(session, "企业所得税法", "企业所得税法#28",
                   "依据财税〔2026〕15号的规定执行。")
    assert [e.origin for e in items] == [CORPUS]
    assert items[0].document_number == "财税〔2026〕15号"


def test_corpus_lookup_ignores_lead_in_verbs(session, corpus, brave_on):
    """Regression: 「根据财税〔2026〕15号」 once extracted as 「根据财税…」,
    which matched nothing in the corpus and forced a needless search."""
    items = gather(session, "企业所得税法", "企业所得税法#28",
                   "根据财税〔2026〕15号第二条。")
    assert [e.origin for e in items] == [CORPUS]


@respx.mock
def test_falls_back_to_search_when_corpus_misses(session, corpus, brave_on):
    respx.get(_ENDPOINT).mock(return_value=httpx.Response(200, json=_brave_payload(
        {"title": "某公告", "description": "摘要", "url": "https://x.gov.cn/1"},
    )))
    items = gather(session, "企业所得税法", "企业所得税法#28", "没有任何文号的条文。")
    assert items and all(e.origin == SEARCH for e in items)


@respx.mock
def test_can_request_both_sources(session, corpus, brave_on):
    respx.get(_ENDPOINT).mock(return_value=httpx.Response(200, json=_brave_payload(
        {"title": "某公告", "description": "摘要", "url": "https://x.gov.cn/1"},
    )))
    items = gather(session, "企业所得税法", "企业所得税法#28",
                   "依据财税〔2026〕15号。", search_when_corpus_hits=True)
    assert {e.origin for e in items} == {CORPUS, SEARCH}


def test_works_without_a_session(brave_off):
    """Analysis must still run when no corpus is configured."""
    assert gather(None, "企业所得税法", "企业所得税法#28", "依据财税〔2026〕15号。") == []


def test_multiple_citations_resolved_in_order(session, corpus, brave_on):
    items = gather(session, "企业所得税法", "企业所得税法#28",
                   "废止国税发〔2003〕67号，改依财税〔2026〕15号办理。")
    assert [e.document_number for e in items] == [
        "国税发〔2003〕67号", "财税〔2026〕15号",
    ]


# ---------- formatting ----------

def test_format_empty_tells_the_model_to_lower_confidence():
    text = format_evidence([])
    assert "查無外部資料" in text
    assert "confidence" in text


def test_corpus_evidence_is_marked_authoritative():
    text = format_evidence([Evidence(
        origin=CORPUS, title="某公告", snippet="正文", url="https://a",
        document_number="财税〔2026〕15号", written_date="2026-01-15",
        aging="全文有效", corpus_version="2026-02-27",
    )])
    assert "可視為權威原文" in text
    assert "财税〔2026〕15号" in text
    # The non-official caveat must not be attached to corpus text.
    assert "非官方原文" not in text


def test_search_evidence_keeps_the_non_official_caveat():
    text = format_evidence([Evidence(
        origin=SEARCH, title="某新聞", snippet="摘要", url="https://b",
    )])
    assert "非官方原文" in text
    assert "可視為權威原文" not in text


def test_repealed_documents_are_flagged(session):
    text = format_evidence([Evidence(
        origin=CORPUS, title="已废止的旧通知", snippet="旧的规定。",
        url="https://b", document_number="国税发〔2003〕67号",
        aging="全文废止", corpus_version="2026-02-27",
    )])
    assert "⛔" in text
    assert "不得作為現行有效依據" in text
    # The status is only true as of the crawl date, and must say so.
    assert "2026-02-27" in text


def test_aging_stamp_omitted_when_version_unknown():
    text = format_evidence([Evidence(
        origin=CORPUS, title="t", snippet="s", url="u", aging="全文有效",
    )])
    assert "全文有效" in text
    assert "截至" not in text


def test_both_sections_rendered_separately():
    text = format_evidence([
        Evidence(origin=CORPUS, title="原文", snippet="a", url="https://a"),
        Evidence(origin=SEARCH, title="搜尋", snippet="b", url="https://b"),
    ])
    assert text.index("法規原文") < text.index("網路搜尋結果")


def test_evidence_repealed_property():
    assert Evidence(CORPUS, "t", "s", "u", aging="全文废止").is_repealed
    assert Evidence(CORPUS, "t", "s", "u", aging="全文失效").is_repealed
    assert not Evidence(CORPUS, "t", "s", "u", aging="全文有效").is_repealed
    assert not Evidence(CORPUS, "t", "s", "u", aging="").is_repealed


def test_corpus_failure_degrades_to_search(session, corpus, brave_off, monkeypatch):
    """A broken corpus must not take the analysis run down with it."""
    def boom(*_a, **_k):
        raise RuntimeError("corpus table missing")

    monkeypatch.setattr(evidence.corpus_store, "lookup_many", boom)
    assert gather(session, "企业所得税法", "企业所得税法#28", "依据财税〔2026〕15号。") == []
