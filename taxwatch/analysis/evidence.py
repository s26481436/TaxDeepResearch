"""Evidence gathering for change analysis: local corpus first, web search second.

The two sources are not interchangeable and the prompt must not treat them as
such:

- **Corpus** entries are the official text from a government 法規庫. They can
  be quoted as authoritative, and they carry a repeal status.
- **Search** results are third-party snippets. They hint at where to look;
  they are not the law.

Searching is also the expensive half, so anything the corpus can answer never
reaches the network.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from taxwatch.analysis import brave_search
from taxwatch.corpus import store as corpus_store
from taxwatch.models import CorpusDocument
from taxwatch.wenhao import extract_all

logger = logging.getLogger(__name__)

CORPUS = "corpus"
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
        return self.origin == CORPUS

    @property
    def is_repealed(self) -> bool:
        return self.aging in ("全文废止", "全文失效")


def gather(
    session: Session | None,
    document_title: str,
    node_key: str,
    new_text: str,
    *,
    search_when_corpus_hits: bool = False,
) -> list[Evidence]:
    """Collect evidence for one change.

    Every 文號 cited in the provision is resolved against the corpus. A web
    search runs only when the corpus came up empty — unless the caller asks
    for both.
    """
    found: list[Evidence] = []

    if session is not None:
        found.extend(_from_corpus(session, node_key, new_text))

    if found and not search_when_corpus_hits:
        logger.info("Evidence for %s: %d corpus hit(s), search skipped", node_key, len(found))
        return found

    before = len(found)
    found.extend(_from_search(document_title, node_key, new_text))
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


def _from_search(document_title: str, node_key: str, new_text: str) -> list[Evidence]:
    results = brave_search.gather_results(document_title, node_key, new_text)
    return [
        Evidence(origin=SEARCH, title=r.title, snippet=r.description, url=r.url, written_date=r.age)
        for r in results
    ]


def format_evidence(items: list[Evidence]) -> str:
    """Render evidence for the prompt, keeping the two origins clearly apart."""
    if not items:
        return "## 外部佐證\n\n（查無外部資料，請僅依條文原文分析，並據此下修 confidence）"

    corpus = [e for e in items if e.origin == CORPUS]
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
