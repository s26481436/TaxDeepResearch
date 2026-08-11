"""Normalizer for Chinese tax documents (HTML from chinatax.gov.cn / mof.gov.cn).

CN legal documents have a consistent structure:
  - Title with 文号
  - Body divided by 条 (articles) in numbered form: 第一条、第二条 or 一、二、三
  - Sometimes structured as 章 (chapters) containing 条

Parent-child law hierarchy (子母法):
  法律 > 行政法规(条例) > 部门规章(办法/细则) > 规范性文件(公告/通知/批复)
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from taxwatch.connectors.base import RawDocument
from taxwatch.normalize.base import NormalizedDoc, Normalizer, ProvisionData
from taxwatch.normalize.text import normalize_text

_CN_NUM_MAP = {
    "一": "1",
    "二": "2",
    "三": "3",
    "四": "4",
    "五": "5",
    "六": "6",
    "七": "7",
    "八": "8",
    "九": "9",
    "十": "10",
    "十一": "11",
    "十二": "12",
    "十三": "13",
    "十四": "14",
    "十五": "15",
    "十六": "16",
    "十七": "17",
    "十八": "18",
    "十九": "19",
    "二十": "20",
    "二十一": "21",
    "二十二": "22",
    "二十三": "23",
    "二十四": "24",
    "二十五": "25",
    "二十六": "26",
    "二十七": "27",
    "二十八": "28",
    "二十九": "29",
    "三十": "30",
    "三十一": "31",
    "三十二": "32",
    "三十三": "33",
    "三十四": "34",
    "三十五": "35",
    "三十六": "36",
    "三十七": "37",
    "三十八": "38",
    "三十九": "39",
    "四十": "40",
    "四十一": "41",
    "四十二": "42",
    "四十三": "43",
    "四十四": "44",
    "四十五": "45",
    "四十六": "46",
    "四十七": "47",
    "四十八": "48",
    "四十九": "49",
    "五十": "50",
}


class CnTaxHtmlNormalizer(Normalizer):
    def normalize(self, raw: RawDocument) -> NormalizedDoc:
        encoding = _detect_encoding(raw)
        html = raw.content.decode(encoding, errors="replace")
        soup = BeautifulSoup(html, "html.parser")

        # The connector's listing title is authoritative; fgk article pages put
        # the site name in <title>, so only fall back to scraping the document.
        title = (raw.metadata.get("title") or "").strip() or self._extract_title(soup)
        title = title or raw.external_id
        law_name = _extract_law_name(title)
        provisions = self._extract_provisions(soup, law_name or raw.external_id)

        doc_level = _infer_hierarchy_level(title)

        return NormalizedDoc(
            external_id=raw.external_id,
            title=title,
            provisions=provisions,
            metadata={
                "source_format": "cn_tax_html",
                "hierarchy_level": doc_level,
                "law_name": law_name or "",
            },
        )

    def _extract_title(self, soup: BeautifulSoup) -> str:
        for sel in ["h1", "h2", ".article-title", ".law-title", "#title", "title"]:
            el = soup.select_one(sel)
            if el:
                text = el.get_text(strip=True)
                if text and len(text) > 2:
                    return text
        return ""

    def _extract_provisions(
        self,
        soup: BeautifulSoup,
        doc_key: str,
    ) -> list[ProvisionData]:
        content_div = soup.select_one(
            ".article-content, .law-content, .content, .TRS_Editor, #content, main, article"
        )
        if not content_div:
            content_div = soup.body or soup

        full_text = normalize_text(content_div.get_text("\n", strip=True))
        if not full_text:
            return []

        provisions = self._split_by_tiao(full_text, doc_key)
        if provisions:
            return provisions

        provisions = self._split_by_numbered_sections(full_text, doc_key)
        if provisions:
            return provisions

        return [ProvisionData(node_key=doc_key, heading="全文", text=full_text)]

    def _split_by_tiao(self, text: str, doc_key: str) -> list[ProvisionData]:
        """Split by 第X条 pattern (formal laws and regulations)."""
        pattern = r"(第[一二三四五六七八九十百]+条)"
        parts = re.split(pattern, text)

        if len(parts) < 3:
            return []

        provisions: list[ProvisionData] = []
        i = 1
        while i < len(parts) - 1:
            heading = parts[i].strip()
            body = parts[i + 1].strip()
            article_num = _cn_article_to_num(heading)
            node_key = f"{doc_key}#{article_num}" if article_num else f"{doc_key}#{heading}"
            provisions.append(
                ProvisionData(
                    node_key=node_key,
                    heading=heading,
                    text=normalize_text(body),
                )
            )
            i += 2

        return provisions

    def _split_by_numbered_sections(
        self,
        text: str,
        doc_key: str,
    ) -> list[ProvisionData]:
        """Split by numbered sections like 一、 二、 三、 (common in 公告/通知)."""
        pattern = r"([一二三四五六七八九十]+)、"
        parts = re.split(pattern, text)

        if len(parts) < 3:
            return []

        provisions: list[ProvisionData] = []
        i = 1
        while i < len(parts) - 1:
            cn_num = parts[i].strip()
            body = parts[i + 1].strip()
            num = _CN_NUM_MAP.get(cn_num, cn_num)
            node_key = f"{doc_key}#{num}"
            provisions.append(
                ProvisionData(
                    node_key=node_key,
                    heading=f"{cn_num}、",
                    text=normalize_text(body),
                )
            )
            i += 2

        return provisions


def _cn_article_to_num(heading: str) -> str:
    """Convert 第二十三条 to '23'."""
    m = re.search(r"第([一二三四五六七八九十百]+)条", heading)
    if not m:
        return ""
    cn = m.group(1)
    return _CN_NUM_MAP.get(cn, cn)


def _extract_law_name(title: str) -> str | None:
    """Extract the law name from a document title.

    e.g. '中华人民共和国企业所得税法实施条例' → '企业所得税法实施条例'
    """
    title = re.sub(r"^中华人民共和国", "", title)
    suffixes = [
        "法实施条例",
        "法实施细则",
        "暂行条例实施细则",
        "暂行条例",
        "暂行办法",
        "实施条例",
        "实施细则",
        "管理办法",
        "法",
        "条例",
        "细则",
        "办法",
        "规则",
    ]
    for suffix in suffixes:
        if title.endswith(suffix):
            return title
    return None


def _infer_hierarchy_level(title: str) -> str:
    """Infer the position in the legal hierarchy.

    法律 > 行政法规(条例) > 部门规章(细则/办法) > 规范性文件(公告/通知)
    """
    if re.search(r"法$", title) and "办法" not in title:
        return "law"
    if re.search(r"条例$", title):
        return "regulation"
    if re.search(r"细则$|办法$|规则$", title):
        return "rule"
    if re.search(r"公告|通知|批复", title):
        return "notice"
    return "unknown"


def _detect_encoding(raw: RawDocument) -> str:
    ct = raw.content_type or ""
    m = re.search(r"charset=([^\s;]+)", ct, re.IGNORECASE)
    if m:
        enc = m.group(1).lower().replace("gb2312", "gb18030")
        return enc
    head = raw.content[:500].decode("ascii", errors="replace").lower()
    m = re.search(r'charset=(["\']?)([a-z0-9_-]+)\1', head)
    if m:
        return m.group(2).replace("gb2312", "gb18030")
    return "utf-8"
