"""国家税务总局 connector — 政策法规库 (fgk.chinatax.gov.cn).

The legacy ``www.chinatax.gov.cn/chinatax/n<id>/`` listing pages were retired:
their article URLs now 404 and the policy library moved to the ``fgk`` subdomain,
which renders client-side. Rather than drive a browser, this connector calls the
JSON search backend the fgk pages themselves use::

    GET https://www.chinatax.gov.cn/search5/search/s
        ?siteCode=bm29000002&column=...&label=...&pageNum=&pageSize=

The documents live under ``searchResultAll.searchTotal``; each entry carries the
title, canonical content URL, publication date and effect level (效力層級).
Article bodies are then fetched from the returned ``content.html`` URLs, which
are plain server-rendered HTML.

Uses 文号 (e.g. 国家税务总局公告2026年第16号) as the stable external_id where the
title exposes one, falling back to the numeric id embedded in the URL.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from taxwatch.connectors.base import Connector, DocumentRef, RawDocument
from taxwatch.connectors.http import create_client, fetch_with_retry
from taxwatch.wenhao import extract_first

_SEARCH_API = "https://www.chinatax.gov.cn/search5/search/s"

# The backend rejects requests that don't look like they came from the fgk UI.
_REFERER = "https://fgk.chinatax.gov.cn/zcfgk/c100028/zcwj.html"

# Which fgk sections and document classes to pull. These mirror the query the
# 政策文件 tab issues; narrowing `label` is the main way to scope the crawl.
_DEFAULT_COLUMNS = "政策法规,政策解读,政策指引"
_DEFAULT_LABELS = "法律,行政法规,国务院文件,税务部门规章,税务规范性文件,财税文件"

# The server caps a page at 10 regardless of the requested size.
_PAGE_SIZE = 10
_DEFAULT_MAX_PAGES = 10

_TITLE_DOC_TYPE = (
    ("实施条例", "regulation"),
    ("實施條例", "regulation"),
    ("实施细则", "regulation"),
    ("實施細則", "regulation"),
    ("实施办法", "regulation"),
    ("實施辦法", "regulation"),
    ("施行细则", "regulation"),
    ("施行細則", "regulation"),
    ("的决定", "announcement"),
    ("的決定", "announcement"),
    ("的通知", "announcement"),
    ("的公告", "announcement"),
    ("管理办法", "announcement"),
    ("管理辦法", "announcement"),
    ("的规定", "announcement"),
    ("的規定", "announcement"),
    ("的意见", "announcement"),
    ("的意見", "announcement"),
    ("的批复", "ruling"),
    ("的批復", "ruling"),
    ("的函", "ruling"),
    ("过渡方案", "announcement"),
    ("過渡方案", "announcement"),
    ("试点方案", "announcement"),
    ("試點方案", "announcement"),
    ("税法", "statute"),
    ("暂行条例", "statute"),
    ("暫行條例", "statute"),
)

_LABEL_DOC_TYPE_MAP = {
    "法律": "statute",
    "行政法规": "statute",
    "部门规章": "regulation",
    "税务部门规章": "regulation",
    "国务院文件": "announcement",
    "规范性文件": "announcement",
    "税务规范性文件": "announcement",
    "财税文件": "announcement",
    "公告": "announcement",
    "通知": "announcement",
    "批复": "ruling",
}


class CnChinataxConnector(Connector):
    key = "cn_chinatax"
    country = "CN"

    def _client(self):
        return create_client(timeout=60, headers={"Referer": _REFERER})

    def _search_params(self, page: int, label: str) -> dict[str, str]:
        cfg = self.source_config
        return {
            "siteCode": "bm29000002",
            "searchWord": "",
            "type": "",
            "pageSize": str(_PAGE_SIZE),
            "pageNum": str(page),
            "orderBy": "5",  # 5 = 成文日期倒序
            "column": cfg.get("columns", _DEFAULT_COLUMNS),
            "label": label,
            "likeDoc": "0",
            "wordPlace": "0",
            "indexCode": "1",
        }

    def discover(self, since: datetime | None = None) -> list[DocumentRef]:
        client = self._client()
        max_pages = int(self.source_config.get("max_pages", _DEFAULT_MAX_PAGES))
        keywords = self.source_config.get("keywords") or None
        labels = [
            label.strip()
            for label in (self.source_config.get("labels") or _DEFAULT_LABELS).split(",")
            if label.strip()
        ]

        refs: list[DocumentRef] = []
        seen: set[str] = set()

        for label in labels:
            self._search_label(client, label, max_pages, since, keywords, refs, seen)

        return refs

    def _search_label(
        self,
        client,
        label: str,
        max_pages: int,
        since: datetime | None,
        keywords: list[str] | None,
        refs: list[DocumentRef],
        seen: set[str],
    ) -> None:
        for page in range(max_pages):
            try:
                resp = fetch_with_retry(
                    client, _SEARCH_API, params=self._search_params(page, label)
                )
                entries = resp.json()["searchResultAll"]["searchTotal"]
            except Exception:
                break

            if not entries:
                break

            reached_cutoff = False
            for entry in entries:
                issued_at = _parse_api_date(entry.get("pubDate") or entry.get("cwrq") or "")
                if since and issued_at and issued_at < since:
                    reached_cutoff = True
                    break

                ref = self._to_ref(entry, issued_at, keywords)
                if ref is None or ref.url in seen or ref.external_id in seen:
                    continue
                seen.add(ref.url)
                seen.add(ref.external_id)
                refs.append(ref)

            if reached_cutoff:
                break

    def _to_ref(
        self,
        entry: dict[str, Any],
        issued_at: datetime | None,
        keywords: list[str] | None,
    ) -> DocumentRef | None:
        title = (entry.get("title") or "").strip()
        url = (entry.get("url") or "").strip()
        if not title or not url:
            return None

        if keywords and not any(kw in title for kw in keywords):
            return None

        url = _https(url)
        label = entry.get("label") or entry.get("xxgk_effectLevel") or ""
        wenhao = _extract_wenhao(title) or (entry.get("indexno") or "").strip()

        return DocumentRef(
            external_id=wenhao or _id_from_url(url) or title[:80],
            title=title,
            doc_type=_infer_doc_type(title, label),
            url=url,
            issued_at=issued_at,
            metadata={
                "title": title,
                "wenhao": wenhao,
                "effect_level": label,
                "aging": entry.get("xxgk_aging") or "",
                "pub_name": entry.get("pubName") or "",
                "summary": (entry.get("content") or "").strip(),
            },
        )

    def fetch(self, ref: DocumentRef) -> RawDocument:
        import logging

        import httpx

        client = self._client()
        try:
            resp = fetch_with_retry(client, ref.url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                logging.getLogger(__name__).warning("chinatax document gone: %s", ref.url)
                # Empty content so the pipeline can skip this document.
                return RawDocument(
                    external_id=ref.external_id,
                    content=b"",
                    content_type="text/html",
                    url=ref.url,
                    metadata={**ref.metadata, "skip": True},
                )
            raise

        metadata = dict(ref.metadata)
        # The search API leaves `indexno` blank, but the article page prints the
        # 文号 directly beneath the title — recover it for citation matching.
        if not metadata.get("wenhao"):
            found = _wenhao_from_article(resp.content)
            if found:
                metadata["wenhao"] = found

        return RawDocument(
            external_id=ref.external_id,
            content=resp.content,
            content_type=resp.headers.get("content-type", "text/html"),
            url=ref.url,
            metadata=metadata,
        )


def _wenhao_from_article(content: bytes) -> str:
    """Read the 文号 printed under the title of an fgk article page.

    Only the opening lines are scanned so that a 文号 *cited* in the body can't
    be mistaken for the document's own number.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content.decode("utf-8", errors="replace"), "html.parser")
    body = soup.select_one(".content")
    if body is None:
        return ""
    return _extract_wenhao(body.get_text("\n", strip=True)[:200]) or ""


def _https(url: str) -> str:
    return url.replace("http://", "https://", 1) if url.startswith("http://") else url


def _parse_api_date(raw: str) -> datetime | None:
    """Parse the API's ``2026-07-31 00:00:00`` timestamps."""
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[: len(fmt) + 2].strip(), fmt)
        except ValueError:
            continue
    return None


def _extract_wenhao(text: str) -> str | None:
    """Extract 文號 from a title, e.g. 国家税务总局公告2026年第5号.

    Thin alias over :mod:`taxwatch.wenhao`, which owns the patterns so that
    connectors, citation extraction and corpus lookup all agree on the key.
    """
    return extract_first(text)


def _id_from_url(url: str) -> str | None:
    """Pull the document id out of a fgk content URL.

    e.g. ``.../zcfgk/c100012/c5251620/content.html`` -> ``c5251620``
    """
    m = re.search(r"/(c\d{5,})/content\.html", url)
    if m:
        return m.group(1)
    m = re.search(r"/(\d{8,})/|/t(\d+)_|content_(\d+)", url)
    if m:
        return m.group(1) or m.group(2) or m.group(3)
    return None


def _infer_doc_type(title: str, label: str = "") -> str:
    for keyword, dtype in _TITLE_DOC_TYPE:
        if keyword in title:
            return dtype
    for keyword, dtype in _LABEL_DOC_TYPE_MAP.items():
        if keyword in label:
            return dtype
    return "announcement"
