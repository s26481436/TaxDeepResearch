"""Normalizer for US state tax statute HTML pages.

Each state's HTML is structured differently. This normalizer uses per-state
parsing strategies keyed on `metadata["state"]`. All strategies produce the
same ProvisionData output:
  node_key  = "{state}:{code}-{section}"
  heading   = section number + title
  text      = normalized body text

Supported states: CA, TX, FL, WA, IL, NY (+ generic HTML fallback).
"""
from __future__ import annotations

import re
from typing import Callable

from bs4 import BeautifulSoup, Tag

from taxwatch.connectors.base import RawDocument
from taxwatch.normalize.base import NormalizedDoc, Normalizer, ProvisionData
from taxwatch.normalize.text import normalize_text

_PARSERS: dict[str, Callable[[BeautifulSoup, dict], list[ProvisionData]]] = {}


def _state_parser(state: str):
    def decorator(fn):
        _PARSERS[state] = fn
        return fn
    return decorator


def _el_text(el: Tag | None, sep: str = "\n") -> str:
    if el is None:
        return ""
    return el.get_text(separator=sep, strip=True)


class UsStateTaxHtmlNormalizer(Normalizer):
    def normalize(self, raw: RawDocument) -> NormalizedDoc:
        content = raw.content if isinstance(raw.content, bytes) else raw.content.encode()
        soup = BeautifulSoup(content, "html.parser")
        state = raw.metadata.get("state", "")
        parse_fn = _PARSERS.get(state, _parse_generic)
        provisions = parse_fn(soup, raw.metadata)

        title = raw.metadata.get("title") or _html_title(soup) or raw.external_id
        return NormalizedDoc(
            external_id=raw.external_id,
            title=title,
            provisions=provisions,
            metadata={
                "source_format": "us_state_tax_html",
                "state": state,
                "jurisdiction": raw.metadata.get("jurisdiction", f"US-{state}"),
                "tax_type": raw.metadata.get("tax_type", ""),
                "statute_code": raw.metadata.get("statute_code", ""),
            },
        )


# ---------------------------------------------------------------------------
# California — leginfo.legislature.ca.gov
# ---------------------------------------------------------------------------

@_state_parser("CA")
def _parse_ca(soup: BeautifulSoup, meta: dict) -> list[ProvisionData]:
    section = meta.get("section", "")
    code = meta.get("statute_code", "RTC")

    body = soup.select_one("div.lawcode") or soup.select_one("div#lawcontent") or soup.body
    if not body:
        return []

    text_parts = [normalize_text(_el_text(el)) for el in body.select("p, div.section-text, .lawtext") if _el_text(el)]
    full_text = "\n".join(text_parts) or normalize_text(_el_text(body))

    if not full_text.strip():
        return []
    return [ProvisionData(
        node_key=f"CA:{code}-{section}",
        heading=f"{code} § {section}",
        text=full_text.strip(),
    )]


# ---------------------------------------------------------------------------
# Texas — statutes.capitol.texas.gov
# ---------------------------------------------------------------------------

@_state_parser("TX")
def _parse_tx(soup: BeautifulSoup, meta: dict) -> list[ProvisionData]:
    provisions = []
    chapter = meta.get("chapter", "")
    code = meta.get("statute_code", "TX Tax Code")

    for sec_div in soup.select("div.section, .codeSect"):
        heading_el = sec_div.select_one("b, .section-heading, h3, h4")
        heading = normalize_text(_el_text(heading_el)) if heading_el else ""

        body_parts = [
            normalize_text(_el_text(el))
            for el in sec_div.select("p, .body-text, .text")
            if normalize_text(_el_text(el)) and normalize_text(_el_text(el)) != heading
        ]
        if not body_parts:
            body_parts = [normalize_text(_el_text(sec_div))]

        sec_match = re.search(r"Sec\.?\s*([\d.]+[A-Z]?)", heading)
        sec_no = sec_match.group(1) if sec_match else chapter

        provisions.append(ProvisionData(
            node_key=f"TX:{code}-{sec_no}",
            heading=heading or f"Ch. {chapter}",
            text="\n".join(body_parts).strip(),
        ))

    if not provisions:
        body = soup.body
        if body:
            provisions.append(ProvisionData(
                node_key=f"TX:{code}-{chapter}",
                heading=f"Texas Tax Code Ch. {chapter}",
                text=normalize_text(_el_text(body)),
            ))
    return provisions


# ---------------------------------------------------------------------------
# Florida — flsenate.gov
# ---------------------------------------------------------------------------

@_state_parser("FL")
def _parse_fl(soup: BeautifulSoup, meta: dict) -> list[ProvisionData]:
    provisions = []
    chapter = meta.get("chapter", "")
    code = meta.get("statute_code", "Florida Statutes")

    for sec_div in soup.select("div.section, div.statute-text"):
        heading_el = sec_div.select_one("h2, h3, .section-number")
        heading = normalize_text(_el_text(heading_el)) if heading_el else ""
        body = normalize_text(_el_text(sec_div))
        sec_match = re.search(r"([\d.]+)", heading)
        sec_no = sec_match.group(1) if sec_match else chapter

        if body.strip():
            provisions.append(ProvisionData(
                node_key=f"FL:{code}-{sec_no}",
                heading=heading or f"Ch. {chapter}",
                text=body,
            ))

    if not provisions:
        body_node = soup.select_one("div#siteContent, div.content") or soup.body
        if body_node:
            provisions.append(ProvisionData(
                node_key=f"FL:{code}-{chapter}",
                heading=f"Florida Statutes Ch. {chapter}",
                text=normalize_text(_el_text(body_node)),
            ))
    return provisions


# ---------------------------------------------------------------------------
# Washington — apps.leg.wa.gov WAC
# ---------------------------------------------------------------------------

@_state_parser("WA")
def _parse_wa(soup: BeautifulSoup, meta: dict) -> list[ProvisionData]:
    provisions = []
    chapter = meta.get("chapter", "")
    code = meta.get("statute_code", "WAC")

    for sec_div in soup.select("div.RCWSection, div.WACSection, .lawsection"):
        heading_el = sec_div.select_one(".section-header, b, h3")
        heading = normalize_text(_el_text(heading_el)) if heading_el else ""
        body = normalize_text(_el_text(sec_div))
        sec_match = re.search(r"([\d-]+)", heading)
        sec_no = sec_match.group(1) if sec_match else chapter

        if body.strip():
            provisions.append(ProvisionData(
                node_key=f"WA:{code}-{sec_no}",
                heading=heading or chapter,
                text=body,
            ))

    if not provisions:
        body_node = soup.select_one("div#ctl00_ContentPlaceHolder1_panelText") or soup.body
        if body_node:
            provisions.append(ProvisionData(
                node_key=f"WA:{code}-{chapter}",
                heading=f"WAC {chapter}",
                text=normalize_text(_el_text(body_node)),
            ))
    return provisions


# ---------------------------------------------------------------------------
# Illinois — ilga.gov
# ---------------------------------------------------------------------------

@_state_parser("IL")
def _parse_il(soup: BeautifulSoup, meta: dict) -> list[ProvisionData]:
    provisions = []
    act_id = meta.get("act_id", "")
    code = meta.get("statute_code", "35 ILCS")

    for sec_div in soup.select("div.Section, tr.sectionRow, .ilcssection"):
        heading_el = sec_div.select_one("b, .sectionNumber")
        heading = normalize_text(_el_text(heading_el)) if heading_el else ""
        body = normalize_text(_el_text(sec_div))
        sec_match = re.search(r"Sec\.?\s*([\d.]+)", heading)
        sec_no = sec_match.group(1) if sec_match else act_id

        if body.strip():
            provisions.append(ProvisionData(
                node_key=f"IL:{code}-{sec_no}",
                heading=heading or f"Act {act_id}",
                text=body,
            ))

    if not provisions:
        body_node = soup.select_one("div#content") or soup.body
        if body_node:
            provisions.append(ProvisionData(
                node_key=f"IL:{code}-{act_id}",
                heading=f"35 ILCS Act {act_id}",
                text=normalize_text(_el_text(body_node)),
            ))
    return provisions


# ---------------------------------------------------------------------------
# New York — nysenate.gov
# ---------------------------------------------------------------------------

@_state_parser("NY")
def _parse_ny(soup: BeautifulSoup, meta: dict) -> list[ProvisionData]:
    provisions = []
    article = meta.get("article", "")
    code = meta.get("statute_code", "NY Tax Law")

    for sec_div in soup.select("div.law-section, article.bill-section"):
        heading_el = sec_div.select_one("h3, .section-title, .section-number")
        heading = normalize_text(_el_text(heading_el)) if heading_el else ""
        body = normalize_text(_el_text(sec_div))
        sec_match = re.search(r"§\s*([\d-]+[a-z]?)", heading)
        sec_no = sec_match.group(1) if sec_match else article

        if body.strip():
            provisions.append(ProvisionData(
                node_key=f"NY:{code}-{sec_no}",
                heading=heading or f"Article {article}",
                text=body,
            ))

    if not provisions:
        body_node = soup.select_one("div.law-text, main") or soup.body
        if body_node:
            provisions.append(ProvisionData(
                node_key=f"NY:{code}-Art{article}",
                heading=f"NY Tax Law Article {article}",
                text=normalize_text(_el_text(body_node)),
            ))
    return provisions


# ---------------------------------------------------------------------------
# Generic fallback
# ---------------------------------------------------------------------------

def _parse_generic(soup: BeautifulSoup, meta: dict) -> list[ProvisionData]:
    state = meta.get("state", "US")
    external_id = meta.get("external_id", "")
    body_node = soup.select_one("main, article, div#content, div.content") or soup.body
    if not body_node:
        return []
    return [ProvisionData(
        node_key=f"{state}:{external_id}",
        heading=_html_title(soup) or external_id,
        text=normalize_text(_el_text(body_node)),
    )]


def _html_title(soup: BeautifulSoup) -> str:
    title_el = soup.select_one("title, h1")
    return normalize_text(_el_text(title_el)) if title_el else ""
