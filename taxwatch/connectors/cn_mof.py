"""财政部 connector (mof.gov.cn).

Scrapes 财政部条法司 / 税政司 policy listings.
Focus: enterprise/manufacturing tax regulations and notices.
"""

from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup

from taxwatch.connectors.base import Connector, DocumentRef, RawDocument
from taxwatch.connectors.http import create_client, fetch_with_retry


class CnMofConnector(Connector):
    key = "cn_mof"
    country = "CN"

    def _base_url(self) -> str:
        return self.source_config.get("base_url", "https://www.mof.gov.cn")

    def discover(self, since: datetime | None = None) -> list[DocumentRef]:
        client = create_client(timeout=60, headers={"Accept-Charset": "utf-8"})
        base = self._base_url()
        refs: list[DocumentRef] = []

        list_paths = self.source_config.get(
            "list_paths",
            [
                "/zhengwuxinxi/zhengcefabu/",  # 财政部政策发布（税政司文件 szs.mof.gov.cn）
            ],
        )

        keywords = self.source_config.get(
            "keywords",
            [
                "企业所得税",
                "增值税",
                "印花税",
                "环境保护税",
                "资源税",
                "城市维护建设税",
                "税收",
                "制造业",
                "小微企业",
                "研发费用",
                "加计扣除",
            ],
        )

        for path in list_paths:
            list_url = f"{base}{path}"
            try:
                resp = fetch_with_retry(client, list_url)
                encoding = _detect_encoding(resp)
                html = resp.content.decode(encoding, errors="replace")
                page_refs = self._parse_list_page(html, base, keywords, list_url=list_url)
                refs.extend(page_refs)
            except Exception:
                continue

        return refs

    def fetch(self, ref: DocumentRef) -> RawDocument:
        referer = ref.metadata.get("list_url", self._base_url())
        client = create_client(
            timeout=60,
            headers={"Accept-Charset": "utf-8", "Referer": referer},
        )
        url = ref.url
        resp = fetch_with_retry(client, url)
        return RawDocument(
            external_id=ref.external_id,
            content=resp.content,
            content_type=resp.headers.get("content-type", "text/html"),
            url=url,
            metadata=ref.metadata,
        )

    def _parse_list_page(
        self,
        html: str,
        base_url: str,
        keywords: list[str],
        list_url: str = "",
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

            from taxwatch.connectors.cn_chinatax import _extract_wenhao, _id_from_url

            doc_id = _extract_wenhao(title) or _id_from_url(href) or title[:80]

            parent = link.find_parent(["li", "tr", "div"])
            date = self._extract_date(parent) if parent else None

            refs.append(
                DocumentRef(
                    external_id=doc_id,
                    title=title,
                    doc_type="announcement",
                    url=href,
                    issued_at=date,
                    metadata={
                        "wenhao": doc_id if "号" in doc_id else "",
                        "list_url": list_url,
                    },
                )
            )

        return refs

    @staticmethod
    def _extract_date(element) -> datetime | None:
        text = element.get_text()
        m = re.search(r"(\d{4})[-.年/](\d{1,2})[-.月/](\d{1,2})", text)
        if m:
            try:
                return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                return None
        return None


def _detect_encoding(resp) -> str:
    ct = resp.headers.get("content-type", "")
    m = re.search(r"charset=([^\s;]+)", ct, re.IGNORECASE)
    if m:
        enc = m.group(1).lower().replace("gb2312", "gb18030")
        return enc
    return "utf-8"
