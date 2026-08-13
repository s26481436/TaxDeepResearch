"""国家税务总局 policy-library search — official corroboration, no API quota.

The fgk 進階搜尋 page drives the same JSON backend the crawler already uses
(``search5/search/s``); the only difference is that the crawler leaves
``searchWord`` empty to enumerate a column, while this passes a term to look
something up. That makes the government's own search available to the analysis
stage for free, and it beats a third-party engine on every axis that matters
here:

* **Authoritative.** Results are 税务总局 documents, so a hit can be cited as
  the official record rather than as "a website said so".
* **Carries 时效性.** ``xxgk_aging`` says 全文废止 / 已修改 / 全文有效. A web
  search cannot tell you a regulation was repealed, and that is precisely the
  fact an analyst must not get wrong.
* **Precise.** Searching a 文號 returns exactly that document.

Best-effort like every other evidence source: any failure degrades to "no
official evidence" rather than failing the run.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from taxwatch.config import get_settings
from taxwatch.connectors.http import create_client, fetch_with_retry

logger = logging.getLogger(__name__)

_SEARCH_API = "https://www.chinatax.gov.cn/search5/search/s"

# The backend rejects requests that don't look like they came from the fgk UI.
_REFERER = "https://fgk.chinatax.gov.cn/zcfgk/c100028/zcwj.html"

_COLUMNS = "政策法规,政策解读,政策指引"
_LABELS = (
    "法律,行政法规,国务院文件,税务部门规章,税务规范性文件,"
    "财税文件,文字政策解读,其他文件,工作通知,政策指引"
)

# The backend wraps matched terms in <span> for highlighting.
_TAG_RE = re.compile(r"<[^>]+>")

# How far either side of the amendment to look when searching by keyword. A
# companion 公告 lands within a quarter of the change it accompanies; widening
# this past that mostly pulls in unrelated history.
_DATE_WINDOW_DAYS = 90


@dataclass(frozen=True)
class OfficialResult:
    title: str
    url: str
    document_number: str = ""
    aging: str = ""
    effect_level: str = ""
    pub_date: str = ""
    summary: str = ""

    @property
    def is_repealed(self) -> bool:
        return self.aging in ("全文废止", "全文失效")


def search(
    word: str,
    *,
    count: int | None = None,
    date_from: str = "",
    date_to: str = "",
) -> list[OfficialResult]:
    """Run one official policy-library search. Returns [] on any failure."""
    settings = get_settings()

    if not settings.fgk_search_enabled:
        return []
    # Two characters is a complete query in Chinese (增值稅, 契稅).
    if not word or len(word.strip()) < 2:
        return []

    limit = count or settings.fgk_search_max_results
    params = {
        "siteCode": "bm29000002",
        "searchWord": word.strip(),
        "type": "",
        "pageSize": str(limit),
        "pageNum": "0",
        "orderBy": "5",  # 5 = 成文日期倒序
        "column": _COLUMNS,
        "label": _LABELS,
        "likeDoc": "0",
        "wordPlace": "0",
        "indexCode": "1",
        "participleRule": "5",
        "cwrqStart": date_from,
        "cwrqEnd": date_to,
    }

    try:
        client = create_client(timeout=settings.fgk_search_timeout, headers={"Referer": _REFERER})
        resp = fetch_with_retry(client, _SEARCH_API, params=params)
        entries = resp.json()["searchResultAll"]["searchTotal"]
    except Exception as exc:  # noqa: BLE001 — evidence is best-effort
        logger.warning("fgk search failed for %r: %s", word, exc)
        return []

    results = [_to_result(entry) for entry in entries or []]
    results = [r for r in results if r.url]

    logger.info("fgk search: %d result(s) for %r", len(results), word)
    return results


def gather_results(
    document_title: str,
    wenhao: str = "",
    issued_at: datetime | None = None,
    *,
    count: int | None = None,
) -> list[OfficialResult]:
    """Find the official record for one amended document.

    Tries the most precise angle first and stops as soon as something lands —
    a 文號 identifies a document outright, so once it hits there is nothing a
    broader keyword search can add but noise.
    """
    date_from, date_to = _date_window(issued_at)

    for word in _build_queries(document_title, wenhao):
        results = search(word, count=count, date_from=date_from, date_to=date_to)
        if results:
            return results
    return []


def _build_queries(document_title: str, wenhao: str) -> list[str]:
    """Search angles for a document, most precise first.

    Deliberately no article-level angle: searching 「法名 第N條」 returns
    statute-mirror sites rather than anything corroborating an amendment, so
    it spends a request to learn nothing.
    """
    queries: list[str] = []
    if wenhao:
        queries.append(wenhao.strip())

    title = _core_title(document_title)
    if title:
        queries.append(title)

    seen: set[str] = set()
    unique: list[str] = []
    for q in queries:
        q = q.strip()
        if q and len(q) >= 2 and q not in seen:
            seen.add(q)
            unique.append(q)
    return unique


def _core_title(title: str) -> str:
    """Strip the boilerplate wrapper off a title to leave the searchable core.

    「国家税务总局关于电池消费税征收管理有关事项的公告」 searches far better as
    「电池消费税征收管理」 — the issuing body and the 公告 suffix match half the
    library and drown the actual subject.
    """
    core = (title or "").strip()
    if not core:
        return ""

    core = re.sub(r"^.*?关于", "", core)
    core = re.sub(
        r"(?:有关(?:事项|问题)?)?的?(?:公告|通知|决定|批复|规定|意见|办法|函)$",
        "",
        core,
    )
    core = core.strip("《》 　")
    # If stripping left nothing meaningful, the original title is the better bet.
    return core if len(core) >= 4 else (title or "").strip()


def _date_window(issued_at: datetime | None) -> tuple[str, str]:
    if issued_at is None:
        return "", ""
    delta = timedelta(days=_DATE_WINDOW_DAYS)
    return (
        (issued_at - delta).strftime("%Y-%m-%d"),
        (issued_at + delta).strftime("%Y-%m-%d"),
    )


def _to_result(entry: dict) -> OfficialResult:
    return OfficialResult(
        title=_clean(entry.get("title", "")),
        url=_https(_clean(entry.get("url", ""))),
        document_number=_clean(entry.get("indexno", "")),
        aging=_clean(entry.get("xxgk_aging", "")),
        effect_level=_clean(entry.get("label") or entry.get("xxgk_effectLevel") or ""),
        pub_date=_clean(entry.get("pubDate") or entry.get("cwrq") or "")[:10],
        summary=_clean(entry.get("content", "")),
    )


def _clean(value) -> str:
    """Strip highlight markup, and normalise the API's literal 'null' strings."""
    if value is None:
        return ""
    text = _TAG_RE.sub("", str(value)).strip()
    return "" if text == "null" else text


def _https(url: str) -> str:
    return url.replace("http://", "https://", 1) if url.startswith("http://") else url
