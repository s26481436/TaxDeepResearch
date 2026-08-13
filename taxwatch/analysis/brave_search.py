"""Brave Search API client — fallback corroboration, metered.

Only reached when neither the local corpus nor the official policy library
(:mod:`taxwatch.analysis.fgk_search`) has anything to say. Results are
third-party snippets: they hint at where to look, they are not the law.

Because it is metered, three things bound what a run can spend:

* **Query shape.** One search per *document*, not per article. External
  corroboration is a property of the amendment event — a statute revised in
  fifty places is still one event, and asking fifty times returns the same
  page fifty times.
* **Cache.** Responses are stored with a TTL, so the daily re-crawl of an
  unchanged corpus re-bills nothing.
* **Budget and rate.** A hard per-run ceiling, and a minimum interval between
  calls so the free tier's one-per-second limit returns results rather than
  429s.

Search is strictly best-effort: any failure degrades to "no external
evidence" rather than failing the analysis run.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from sqlalchemy.orm import Session

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

    @classmethod
    def from_dict(cls, data: dict) -> SearchResult:
        return cls(
            title=data.get("title", ""),
            description=data.get("description", ""),
            url=data.get("url", ""),
            age=data.get("age", ""),
        )


class QuotaBudget:
    """A per-run ceiling on billable queries.

    Exhaustion is not an error: the analysis degrades to whatever evidence it
    already has. Losing corroboration on the tail of a large run is a far
    better outcome than spending the month's quota on it.
    """

    def __init__(self, limit: int):
        self.limit = limit
        self.spent = 0
        self._lock = threading.Lock()

    def take(self) -> bool:
        with self._lock:
            if self.limit >= 0 and self.spent >= self.limit:
                return False
            self.spent += 1
            return True

    @property
    def exhausted(self) -> bool:
        return self.limit >= 0 and self.spent >= self.limit

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.spent) if self.limit >= 0 else -1


_budget: QuotaBudget | None = None
_last_call_at: float = 0.0
_throttle_lock = threading.Lock()

# Answers already paid for during this run, keyed by query. The persistent
# cache needs a Session; this does not, so a caller that has no database — or
# one that asks the same thing from several places — still pays only once.
_run_memo: dict[str, list[SearchResult]] = {}


def start_run(limit: int | None = None) -> QuotaBudget:
    """Open a fresh budget for one pipeline run."""
    global _budget
    if limit is None:
        limit = get_settings().brave_search_max_queries_per_run
    _budget = QuotaBudget(limit)
    _run_memo.clear()
    logger.info("Brave Search budget for this run: %d queries", limit)
    return _budget


def get_budget() -> QuotaBudget:
    global _budget
    if _budget is None:
        _budget = QuotaBudget(get_settings().brave_search_max_queries_per_run)
    return _budget


def reset_budget() -> None:
    global _budget
    _budget = None
    _run_memo.clear()


def _throttle(min_interval: float) -> None:
    """Space calls out so the free tier answers instead of returning 429."""
    global _last_call_at
    if min_interval <= 0:
        return
    with _throttle_lock:
        elapsed = time.monotonic() - _last_call_at
        if _last_call_at and elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        _last_call_at = time.monotonic()


def _query_hash(query: str) -> str:
    return hashlib.sha256(query.strip().encode()).hexdigest()


def _cache_get(session: Session | None, query: str) -> list[SearchResult] | None:
    if session is None:
        return None
    from taxwatch.models import SearchCache

    ttl_days = get_settings().brave_search_cache_ttl_days
    try:
        row = (
            session.query(SearchCache)
            .filter_by(provider="brave", query_hash=_query_hash(query))
            .first()
        )
    except Exception as exc:  # noqa: BLE001 — the cache is optional infrastructure
        logger.debug("Search cache lookup failed: %s", exc)
        return None

    if row is None:
        return None
    if ttl_days >= 0 and row.fetched_at < datetime.utcnow() - timedelta(days=ttl_days):
        return None
    return [SearchResult.from_dict(d) for d in (row.results or [])]


def _cache_put(session: Session | None, query: str, results: list[SearchResult]) -> None:
    if session is None:
        return
    from taxwatch.models import SearchCache

    try:
        digest = _query_hash(query)
        row = session.query(SearchCache).filter_by(provider="brave", query_hash=digest).first()
        if row is None:
            row = SearchCache(provider="brave", query_hash=digest, query=query)
            session.add(row)
        row.results = [r.to_dict() for r in results]
        row.fetched_at = datetime.utcnow()
        session.flush()
    except Exception as exc:  # noqa: BLE001 — never fail analysis over a cache write
        logger.debug("Search cache write failed: %s", exc)


def search(
    query: str,
    *,
    count: int | None = None,
    session: Session | None = None,
) -> list[SearchResult]:
    """Run one Brave web search. Returns [] when disabled, capped, or on failure."""
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

    key = query.strip()
    if key in _run_memo:
        logger.debug("Brave Search in-run memo hit for %r", query)
        return _run_memo[key]

    cached = _cache_get(session, query)
    if cached is not None:
        logger.debug("Brave Search cache hit for %r", query)
        _run_memo[key] = cached
        return cached

    budget = get_budget()
    if not budget.take():
        logger.warning("Brave Search budget exhausted (%d used); skipping %r", budget.spent, query)
        return []

    limit = count or settings.brave_search_max_results
    _throttle(settings.brave_search_min_interval)

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

    _run_memo[key] = results
    _cache_put(session, query, results)
    logger.info("Brave Search: %d result(s) for %r", len(results), query)
    return results


def gather_results(
    document_title: str,
    node_key: str = "",
    new_text: str = "",
    *,
    per_query: int = 3,
    session: Session | None = None,
) -> list[SearchResult]:
    """Search the amendment behind one document and merge results, de-duplicated."""
    merged: dict[str, SearchResult] = {}
    for query in build_queries(document_title, node_key, new_text):
        for result in search(query, count=per_query, session=session):
            merged.setdefault(result.url, result)
    return list(merged.values())


def build_queries(document_title: str, node_key: str = "", new_text: str = "") -> list[str]:
    """The search angles for one amended document.

    One angle, at document level. The two that used to accompany it are gone
    because measurement did not support them:

    * ``法名 第N條`` returned statute-mirror sites, never corroboration, and
      cost one request per changed article.
    * A verbatim slice of the provision is not a question a search engine can
      answer, and it was unique per article — so it scaled with the size of
      the amendment while yielding almost nothing.

    ``node_key`` and ``new_text`` are still accepted so callers need not change,
    and ``node_key`` supplies the law name when no title was passed.
    """
    law = document_title.strip() if document_title else node_key.split("#", 1)[0].strip()
    if not law or len(law) < 2:
        return []
    return [f"{law} 修正 生效"]


def _clean(text: str) -> str:
    return _TAG_RE.sub("", text).strip()
