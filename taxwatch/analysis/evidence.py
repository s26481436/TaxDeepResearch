"""Evidence gathering for change analysis, cheapest and most authoritative first.

Three sources, which the prompt must not treat as interchangeable:

- **Corpus** entries are the official text from a government 法規庫, held
  locally. Quotable as authoritative, carry a repeal status, cost nothing.
- **Official** results come from the 国家税务总局 policy library, searched live.
  Also authoritative and also unmetered, but we hold the title, 时效性 and
  link rather than the full text.
- **Search** results are third-party snippets from a metered API. They hint at
  where to look; they are not the law.

The order is the point. Each tier is consulted only when the ones above it
came up empty, so the metered tier — the only one that can run out — is
reached last and rarely.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from taxwatch.analysis import brave_search, fgk_search
from taxwatch.corpus import store as corpus_store
from taxwatch.models import CorpusDocument
from taxwatch.wenhao import extract_all

logger = logging.getLogger(__name__)

CORPUS = "corpus"
OFFICIAL = "official"
SEARCH = "search"

_SNIPPET_CHARS = 600


@dataclass(frozen=True)
class Evidence:
    origin: str  # CORPUS | SEARCH
    title: str
    snippet: str
    url: str
    document_number: str = ""
    written_date: str = ""
    aging: str = ""
    corpus_version: str = ""

    @property
    def is_authoritative(self) -> bool:
        return self.origin in (CORPUS, OFFICIAL)

    @property
    def is_repealed(self) -> bool:
        return self.aging in ("全文废止", "全文失效")


def gather_for_document(
    document_title: str,
    wenhao: str = "",
    issued_at: datetime | None = None,
    *,
    session: Session | None = None,
    allow_metered: bool = True,
) -> list[Evidence]:
    """External evidence for one amended document, shared by all its changes.

    Called once per document rather than once per changed article, because
    that is the granularity the evidence actually has: a statute revised in
    fifty places was revised by one act, announced once. Asking per article
    bought fifty copies of the same answer.

    `allow_metered` gates only the fallback. The official library is free, so
    it is consulted either way.
    """
    official = [
        _from_official(r) for r in fgk_search.gather_results(document_title, wenhao, issued_at)
    ]
    if official:
        logger.info(
            "Document evidence for %r: %d official hit(s), metered search skipped",
            document_title,
            len(official),
        )
        return official

    if not allow_metered:
        logger.info(
            "Document evidence for %r: 0 official, metered search not permitted",
            document_title,
        )
        return []

    results = brave_search.gather_results(document_title, session=session)
    logger.info("Document evidence for %r: 0 official, %d search", document_title, len(results))
    return [
        Evidence(origin=SEARCH, title=r.title, snippet=r.description, url=r.url, written_date=r.age)
        for r in results
    ]


def gather(
    session: Session | None,
    document_title: str,
    node_key: str,
    new_text: str,
    *,
    search_when_corpus_hits: bool = False,
    document_evidence: list[Evidence] | None = None,
) -> list[Evidence]:
    """Collect evidence for one change.

    Every 文號 cited in the provision is resolved against the corpus — that
    stays per-provision, since it is a local lookup and the citations differ
    article by article.

    External evidence does not: pass `document_evidence` from
    :func:`gather_for_document` and no network call is made here at all. It is
    only when a caller omits it that this falls back to fetching per change.
    """
    found: list[Evidence] = []

    if session is not None:
        found.extend(_from_corpus(session, node_key, new_text))

    if found and not search_when_corpus_hits:
        logger.info("Evidence for %s: %d corpus hit(s), search skipped", node_key, len(found))
        return found

    if document_evidence is not None:
        found.extend(document_evidence)
        return found

    before = len(found)
    found.extend(_from_search(document_title, node_key, new_text, session=session))
    logger.info("Evidence for %s: %d corpus, %d search", node_key, before, len(found) - before)
    return found


def _from_corpus(session: Session, node_key: str, new_text: str) -> list[Evidence]:
    citations = extract_all(new_text)
    # The provision's own 文號 is worth resolving too — it carries the
    # repeal status and the official tax-type label.
    citations.extend(extract_all(node_key))
    if not citations:
        return []

    try:
        hits = corpus_store.lookup_many(session, citations)
    except Exception as exc:  # noqa: BLE001 — corpus is optional infrastructure
        logger.warning("Corpus lookup failed: %s", exc)
        return []

    # Preserve citation order rather than dict order.
    ordered: list[Evidence] = []
    seen: set[str] = set()
    for key in citations:
        doc = hits.get(key)
        if doc is not None and key not in seen:
            seen.add(key)
            ordered.append(_from_document(doc))
    return ordered


def _from_document(doc: CorpusDocument) -> Evidence:
    return Evidence(
        origin=CORPUS,
        title=doc.title,
        snippet=doc.content[:_SNIPPET_CHARS],
        url=doc.url,
        document_number=doc.document_number,
        written_date=doc.written_date.date().isoformat() if doc.written_date else "",
        aging=doc.aging,
        corpus_version=doc.corpus_version,
    )


def _from_official(result: fgk_search.OfficialResult) -> Evidence:
    return Evidence(
        origin=OFFICIAL,
        title=result.title,
        snippet=result.summary[:_SNIPPET_CHARS],
        url=result.url,
        document_number=result.document_number,
        written_date=result.pub_date,
        aging=result.aging,
    )


def _from_search(
    document_title: str,
    node_key: str,
    new_text: str,
    *,
    session: Session | None = None,
) -> list[Evidence]:
    """Official library first; the metered API only if it found nothing."""
    official = fgk_search.gather_results(document_title or node_key.split("#", 1)[0])
    if official:
        return [_from_official(r) for r in official]

    results = brave_search.gather_results(document_title, node_key, new_text, session=session)
    return [
        Evidence(origin=SEARCH, title=r.title, snippet=r.description, url=r.url, written_date=r.age)
        for r in results
    ]


def format_evidence(items: list[Evidence]) -> str:
    """Render evidence for the prompt, keeping the three origins clearly apart."""
    if not items:
        return "## 外部佐證\n\n（查無外部資料，請僅依條文原文分析，並據此下修 confidence）"

    corpus = [e for e in items if e.origin == CORPUS]
    official = [e for e in items if e.origin == OFFICIAL]
    search = [e for e in items if e.origin == SEARCH]
    lines: list[str] = []

    if corpus:
        lines.append("## 法規原文（本地法規庫，可視為權威原文）")
        lines.append("")
        for i, e in enumerate(corpus, start=1):
            lines.append(f"{i}. **{e.title}**")
            if e.document_number:
                lines.append(f"   文號：{e.document_number}")
            if e.written_date:
                lines.append(f"   成文日期：{e.written_date}")
            if e.aging:
                marker = "⛔ " if e.is_repealed else ""
                stamp = f"（截至 {e.corpus_version}）" if e.corpus_version else ""
                lines.append(f"   時效性：{marker}{e.aging}{stamp}")
            if e.snippet:
                lines.append(f"   原文節錄：{e.snippet}")
            if e.url:
                lines.append(f"   來源：{e.url}")
            lines.append("")
        if any(e.is_repealed for e in corpus):
            lines.append(
                "⚠️ 上列標記為廢止／失效的法規不得作為現行有效依據引用；"
                "若本次異動仍援引該法規，請在 risk_assessment 中指出。"
            )
            lines.append("")

    if official:
        lines.append("## 官方法規庫檢索結果（国家税务总局，官方來源）")
        lines.append("")
        for i, e in enumerate(official, start=1):
            lines.append(f"{i}. **{e.title}**")
            if e.document_number:
                lines.append(f"   文號：{e.document_number}")
            if e.written_date:
                lines.append(f"   成文日期：{e.written_date}")
            if e.aging:
                marker = "⛔ " if e.is_repealed else ""
                lines.append(f"   時效性：{marker}{e.aging}")
            if e.snippet:
                lines.append(f"   摘要：{e.snippet}")
            if e.url:
                lines.append(f"   來源：{e.url}")
            lines.append("")
        lines.append(
            "ℹ️ 以上為官方法規庫的檢索結果：標題、文號與時效性為官方資料，可據以引用；"
            "但此處僅有摘要而非全文，若需引用具體條文內容，請於 citations 標註連結"
            "並說明未取得全文。"
        )
        lines.append("")
        if any(e.is_repealed for e in official):
            lines.append(
                "⚠️ 上列標記為廢止／失效的法規不得作為現行有效依據引用；"
                "若本次異動仍援引該法規，請在 risk_assessment 中指出。"
            )
            lines.append("")

    if search:
        lines.append("## 網路搜尋結果（第三方摘要，非官方原文）")
        lines.append("")
        for i, e in enumerate(search, start=1):
            lines.append(f"{i}. **{e.title}**")
            if e.snippet:
                lines.append(f"   摘要：{e.snippet}")
            if e.written_date:
                lines.append(f"   時間：{e.written_date}")
            lines.append(f"   來源：{e.url}")
            lines.append("")
        lines.append(
            "⚠️ 以上為搜尋引擎結果，非官方原文。僅在與條文原文一致時採用；"
            "若與原文衝突，以原文為準並在分析中指出矛盾。"
        )

    return "\n".join(lines).rstrip()
