"""Brave Search integration — external corroboration for change analysis.

The diff tells us *what* the text says now; it cannot tell us whether an
official announcement confirms the effective date, or whether a companion
公告 was issued alongside it. Brave Search supplies that outside evidence so
the LLM can cite something beyond the provision itself.

Search is strictly best-effort: any failure degrades to "no external
evidence" rather than failing the analysis run.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

from taxwatch.config import get_settings

logger = logging.getLogger(__name__)

_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class SearchResult:
    title: str
    description: str
    url: str
    age: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "description": self.description,
            "url": self.url,
            "age": self.age,
        }


def search(query: str, *, count: int | None = None) -> list[SearchResult]:
    """Run one Brave web search. Returns [] when disabled or on any failure."""
    settings = get_settings()

    if not settings.brave_search_enabled:
        return []
    if not settings.brave_search_api_key:
        logger.debug("Brave Search enabled but no API key configured; skipping")
        return []
    # Two characters is a complete query in Chinese (關稅, 契稅), so the floor
    # has to be lower than it would be for a Latin-script term.
    if not query or len(query.strip()) < 2:
        return []

    limit = count or settings.brave_search_max_results

    try:
        with httpx.Client(timeout=settings.brave_search_timeout) as client:
            resp = client.get(
                _ENDPOINT,
                params={"q": query, "count": limit},
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": settings.brave_search_api_key,
                },
            )
            resp.raise_for_status()
            payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Brave Search failed for %r: %s", query, exc)
        return []

    results = [
        SearchResult(
            title=_clean(item.get("title", "")),
            description=_clean(item.get("description", "")),
            url=item.get("url", ""),
            age=item.get("age", "") or "",
        )
        for item in payload.get("web", {}).get("results", [])
    ]
    results = [r for r in results if r.url]

    logger.info("Brave Search: %d result(s) for %r", len(results), query)
    return results


def gather_evidence(
    document_title: str,
    node_key: str,
    new_text: str,
    *,
    per_query: int = 3,
) -> list[SearchResult]:
    """Search a few angles on one change and merge the results, de-duplicated."""
    merged: dict[str, SearchResult] = {}
    for query in build_queries(document_title, node_key, new_text):
        for result in search(query, count=per_query):
            merged.setdefault(result.url, result)
    return list(merged.values())


def build_queries(document_title: str, node_key: str, new_text: str) -> list[str]:
    """Derive the search angles for one change: the article, the law, the wording."""
    queries: list[str] = []
    article = node_key.split("#", 1)[-1] if "#" in node_key else ""
    law = node_key.split("#", 1)[0] if "#" in node_key else document_title

    if law and article:
        queries.append(f"{law} 第{article}條" if _is_numeric(article) else f"{law} {article}")
    if document_title:
        queries.append(f"{document_title} 修正 生效")
    snippet = (new_text or "").strip().replace("\n", " ")
    if len(snippet) >= 12:
        queries.append(snippet[:60])

    seen: set[str] = set()
    unique: list[str] = []
    for q in queries:
        q = q.strip()
        if q and q not in seen:
            seen.add(q)
            unique.append(q)
    return unique


def format_evidence(results: list[SearchResult]) -> str:
    """Render results as the prompt's 外部佐證 section."""
    if not results:
        return (
            "## 外部佐證（Brave Search）\n\n"
            "（查無外部資料，請僅依條文原文分析，並據此下修 confidence）"
        )

    lines = ["## 外部佐證（Brave Search）", ""]
    for i, r in enumerate(results, start=1):
        lines.append(f"{i}. **{r.title}**")
        if r.description:
            lines.append(f"   摘要：{r.description}")
        if r.age:
            lines.append(f"   時間：{r.age}")
        lines.append(f"   來源：{r.url}")
        lines.append("")
    lines.append(
        "⚠️ 以上為搜尋引擎結果，非官方原文。僅在與條文原文一致時採用；"
        "若與原文衝突，以原文為準並在分析中指出矛盾。"
    )
    return "\n".join(lines)


def _clean(text: str) -> str:
    return _TAG_RE.sub("", text).strip()


def _is_numeric(value: str) -> bool:
    return bool(re.fullmatch(r"\d+(?:-\d+)?", value))
