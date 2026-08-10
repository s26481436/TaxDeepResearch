"""Normalizer for Taiwan MOF rulings and constitutional interpretations (HTML)."""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from taxwatch.connectors.base import RawDocument
from taxwatch.normalize.base import NormalizedDoc, Normalizer, ProvisionData
from taxwatch.normalize.text import normalize_text


class TwRulingHtmlNormalizer(Normalizer):
    def normalize(self, raw: RawDocument) -> NormalizedDoc:
        html = raw.content.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "html.parser")

        title = self._extract_title(soup) or raw.external_id
        provisions = self._extract_provisions(soup, raw.external_id)

        return NormalizedDoc(
            external_id=raw.external_id,
            title=title,
            provisions=provisions,
            metadata={"source_format": "tw_ruling_html"},
        )

    def _extract_title(self, soup: BeautifulSoup) -> str:
        for sel in ["h2", "h3", ".title", "#law-name", "title"]:
            el = soup.select_one(sel)
            if el:
                text = el.get_text(strip=True)
                if text:
                    return text
        return ""

    def _extract_provisions(self, soup: BeautifulSoup, external_id: str) -> list[ProvisionData]:
        provisions: list[ProvisionData] = []

        content_div = soup.select_one(".law-content, .content, #content, main, article")
        if not content_div:
            content_div = soup.body or soup

        full_text = normalize_text(content_div.get_text("\n", strip=True))
        if not full_text:
            return provisions

        heading_pat = r"((?:主\s*旨|說\s*明|理\s*由|解釋文|理由書|主文|事實|爭點)(?:：|:))"
        sections = re.split(heading_pat, full_text)

        if len(sections) <= 1:
            provisions.append(ProvisionData(
                node_key=external_id,
                heading="全文",
                text=full_text,
            ))
            return provisions

        i = 1
        while i < len(sections):
            heading = sections[i].strip()
            body = sections[i + 1].strip() if i + 1 < len(sections) else ""
            clean_heading = re.sub(r"[：:]$", "", heading)
            node_key = f"{external_id}#{clean_heading}"
            provisions.append(ProvisionData(
                node_key=node_key,
                heading=clean_heading,
                text=normalize_text(body),
            ))
            i += 2

        return provisions
