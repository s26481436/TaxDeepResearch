"""国家税务总局 connector (chinatax.gov.cn).

Scrapes the policy document listing pages. Documents are HTML with occasional
PDF attachments. Uses 文号 (e.g. 国家税务总局公告2026年第X号) as stable external_id.

Focus: enterprise/manufacturing tax — 企业所得税, 增值税, 印花税, 环保税, 资源税.
"""
from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup

from taxwatch.connectors.base import Connector, DocumentRef, RawDocument
from taxwatch.connectors.http import create_client, fetch_with_retry
from taxwatch.wenhao import extract_first

_DOC_TYPE_MAP = {
    "法律": "statute",
    "行政法规": "statute",
    "部门规章": "regulation",
    "规范性文件": "regulation",
    "税务部门规章": "regulation",
    "公告": "announcement",
    "通知": "announcement",
    "批复": "ruling",
}


class CnChinataxConnector(Connector):
    key = "cn_chinatax"
    country = "CN"

    def _base_url(self) -> str:
        return self.source_config.get("base_url", "https://www.chinatax.gov.cn")

    def discover(self, since: datetime | None = None) -> list[DocumentRef]:
        client = create_client(timeout=60, headers={"Accept-Charset": "utf-8"})
        base = self._base_url()
        refs: list[DocumentRef] = []

        list_paths = self.source_config.get("list_paths", [
            "/chinatax/n810341/n810755/index.html",   # 税收法规
            "/chinatax/n810341/n810765/index.html",   # 税务部门规章
            "/chinatax/n810341/n810825/index.html",   # 规范性文件
        ])

        keywords = self.source_config.get("keywords", [
            "企业所得税", "增值税", "印花税", "环境保护税", "资源税",
            "城市维护建设税", "税收征收管理", "制造业", "小微企业",
            "研发费用", "加计扣除", "留抵退税", "出口退税",
        ])

        for path in list_paths:
            try:
                resp = fetch_with_retry(client, f"{base}{path}")
                html = resp.content.decode("utf-8", errors="replace")
                page_refs = self._parse_list_page(html, base, keywords)
                refs.extend(page_refs)
            except Exception:
                continue

        return refs

    def fetch(self, ref: DocumentRef) -> RawDocument:
        import logging
        import httpx

        client = create_client(timeout=60, headers={"Accept-Charset": "utf-8"})
        url = ref.url
        try:
            resp = fetch_with_retry(client, url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                logging.getLogger(__name__).warning(
                    "chinatax document URL returned 404 (page may have moved to fgk subdomain): %s", url
                )
                # Return empty content so pipeline can skip gracefully
                return RawDocument(
                    external_id=ref.external_id,
                    content=b"",
                    content_type="text/html",
                    url=url,
                    metadata={**ref.metadata, "skip": True},
                )
            raise
        return RawDocument(
            external_id=ref.external_id,
            content=resp.content,
            content_type=resp.headers.get("content-type", "text/html"),
            url=url,
            metadata=ref.metadata,
        )

    def _parse_list_page(
        self, html: str, base_url: str, keywords: list[str],
    ) -> list[DocumentRef]:
        soup = BeautifulSoup(html, "html.parser")
        refs: list[DocumentRef] = []

        for link in soup.select("a[href]"):
            title = link.get_text(strip=True)
            if not title or len(title) < 4:
                continue

            if keywords and not any(kw in title for kw in keywords):
                continue

            href = link["href"]
            if not href.startswith("http"):
                if href.startswith("/"):
                    href = f"{base_url}{href}"
                else:
                    href = f"{base_url}/{href}"

            doc_id = _extract_wenhao(title) or _id_from_url(href) or title[:80]
            doc_type = _infer_doc_type(title)

            parent = link.find_parent(["li", "tr", "div"])
            date = _extract_date_from_context(parent) if parent else None

            refs.append(DocumentRef(
                external_id=doc_id,
                title=title,
                doc_type=doc_type,
                url=href,
                issued_at=date,
                metadata={"wenhao": doc_id if doc_id != title[:80] else ""},
            ))

        return refs


def _extract_wenhao(text: str) -> str | None:
    """Extract 文號 from a title, e.g. 国家税务总局公告2026年第5号.

    Thin alias over :mod:`taxwatch.wenhao`, which owns the patterns so that
    connectors, citation extraction and corpus lookup all agree on the key.
    """
    return extract_first(text)


def _id_from_url(url: str) -> str | None:
    m = re.search(r"/(\d{8,})/|/t(\d+)_|content_(\d+)", url)
    if m:
        return m.group(1) or m.group(2) or m.group(3)
    return None


def _infer_doc_type(title: str) -> str:
    for keyword, dtype in _DOC_TYPE_MAP.items():
        if keyword in title:
            return dtype
    return "announcement"


def _extract_date_from_context(element) -> datetime | None:
    text = element.get_text()
    m = re.search(r"(\d{4})[-.年/](\d{1,2})[-.月/](\d{1,2})", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None
