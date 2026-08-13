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

from taxwatch.cn_numerals import to_arabic
from taxwatch.connectors.base import RawDocument
from taxwatch.normalize.base import NormalizedDoc, Normalizer, ProvisionData
from taxwatch.normalize.text import normalize_text


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
            ".article-content, .law-content, .TRS_Editor, #content, main, article"
        )
        if not content_div:
            content_div = soup.select_one(".content")
        if not content_div:
            content_div = soup.body or soup

        _strip_boilerplate(content_div)

        full_text = normalize_text(content_div.get_text("\n", strip=True))
        full_text = _remove_boilerplate_text(full_text)
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
        pattern = r"(第[一二三四五六七八九十百千零〇两]+条)"
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
            node_key = f"{doc_key}#{to_arabic(cn_num)}"
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
    m = re.search(r"第([一二三四五六七八九十百千零〇两]+)条", heading)
    if not m:
        return ""
    return to_arabic(m.group(1))


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


# fgk.chinatax.gov.cn pages embed UI widgets (download buttons, share links,
# subscription prompts, sidebar navigation) inside the same .content container
# as the legal text. Strip them before extracting text.

_BOILERPLATE_SELECTORS = [
    # download / share / subscribe buttons
    ".tools", ".toolbar", ".share", ".download", ".subscribe",
    ".btn-group", ".action-bar", ".article-tools",
    # sidebar and navigation
    ".sidebar", ".aside", ".nav", ".breadcrumb", ".menu",
    # font-size toggles
    ".font-size", ".fontsize",
    # related documents / interpretations
    ".related", ".relation", ".associated",
    # footer links
    ".footer", ".copyright",
    # QR codes / scan prompts
    ".qrcode", ".scan",
    # annotations / notes panels that are not part of the statute
    ".annotation-panel",
]

_BOILERPLATE_TEXT_PATTERNS = re.compile(
    r"|".join([
        r"下载文字版",
        r"下载图片版",
        r"字体:\s*【[大中小]】",
        r"【大】\s*【中】\s*【小】",
        r"分享到:",
        r"收藏\s*订阅",
        r"已推送.*?我的订阅",
        r"此稿件无标签.*?订阅更多",
        r"语音播报:",
        r"扫一扫在手机打开当前页",
        r"【打印】\s*【下载】",
        r"纠错或建议",
        r"历史沿革",
        r"关联解读",
        r"关联文件",
        r"关联问答",
        r"关于《.*?》的解读",
        r"个人中心-我的订阅",
        r"进入\s*\"?订阅设置\"?",
    ])
)


def _strip_boilerplate(soup: BeautifulSoup) -> None:
    """Remove UI chrome elements from the content container."""
    for selector in _BOILERPLATE_SELECTORS:
        for el in soup.select(selector):
            el.decompose()

    for a_tag in soup.find_all("a"):
        text = a_tag.get_text(strip=True)
        if text in ("下载文字版", "下载图片版", "打印", "下载", "收藏", "订阅",
                     "分享", "纠错或建议", "历史沿革", "关联解读", "关联文件", "关联问答"):
            parent = a_tag.parent
            a_tag.decompose()
            if parent and parent.name and not parent.get_text(strip=True):
                parent.decompose()


def _remove_boilerplate_text(text: str) -> str:
    """Remove residual boilerplate phrases that survive element stripping."""
    text = _BOILERPLATE_TEXT_PATTERNS.sub("", text)
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines)
