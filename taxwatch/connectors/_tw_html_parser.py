"""Shared HTML parsers for Taiwan government websites."""
from __future__ import annotations

import re
from datetime import datetime

from bs4 import BeautifulSoup

from taxwatch.connectors.base import DocumentRef


def parse_ruling_list(html: str, base_url: str) -> list[DocumentRef]:
    """Parse a listing page of MOF rulings into DocumentRefs."""
    soup = BeautifulSoup(html, "html.parser")
    refs: list[DocumentRef] = []

    for row in soup.select("table tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue

        link = row.find("a", href=True)
        if not link:
            continue

        title = link.get_text(strip=True)
        href = link["href"]
        if not href.startswith("http"):
            href = f"{base_url}/{href.lstrip('/')}"

        doc_id = _extract_doc_id(href) or title[:50]
        date_text = cells[0].get_text(strip=True) if cells else ""

        refs.append(DocumentRef(
            external_id=doc_id,
            title=title,
            doc_type="ruling",
            url=href,
            issued_at=_parse_date_text(date_text),
        ))

    return refs


def parse_interpretation_list(html: str, base_url: str) -> list[DocumentRef]:
    """Parse a listing page of constitutional interpretations."""
    soup = BeautifulSoup(html, "html.parser")
    refs: list[DocumentRef] = []

    for item in soup.select(".interpretation-item, .search-result, table tr"):
        link = item.find("a", href=True)
        if not link:
            continue

        title = link.get_text(strip=True)
        href = link["href"]
        if not href.startswith("http"):
            href = f"{base_url}/{href.lstrip('/')}"

        match = re.search(r"(釋字第\s*\d+\s*號|憲判字第\s*\d+\s*號)", title)
        doc_id = match.group(1) if match else title[:50]

        refs.append(DocumentRef(
            external_id=doc_id,
            title=title,
            doc_type="interpretation",
            url=href,
        ))

    return refs


def _extract_doc_id(url: str) -> str | None:
    m = re.search(r"[?&]id=([^&]+)", url)
    return m.group(1) if m else None


def _parse_date_text(text: str) -> datetime | None:
    m = re.search(r"(\d{2,4})[./\-](\d{1,2})[./\-](\d{1,2})", text)
    if m:
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 200:
            year += 1911
        try:
            return datetime(year, month, day)
        except ValueError:
            return None
    return None
