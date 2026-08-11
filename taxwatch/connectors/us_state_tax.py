"""US State Tax Law connector.

Each US state publishes tax statutes and regulations independently. This
connector is a unified adapter that dispatches to per-state scrapers, all
sharing the same Connector interface.

Architecture
------------
`UsStateTaxConnector` reads `states` from config; each entry specifies which
state adapter to use. Adapters are callables that return list[DocumentRef] and
implement fetch(). Built-in adapters:

  ca  — California Revenue and Taxation Code (leginfo.legislature.ca.gov)
  tx  — Texas Tax Code (statutes.capitol.texas.gov)
  fl  — Florida Statutes Chapters 198/201/212 (flsenate.gov)
  wa  — Washington Administrative Code Title 458 (apps.leg.wa.gov)
  ny  — New York Tax Law (legislation.nysenate.gov — requires API key)
  il  — Illinois Revenue statutes (ilga.gov)

Common sections tracked per state:
  income_tax, sales_tax, estate_tax, corporate_tax, franchise_tax

Each DocumentRef has:
  external_id  — "{state_abbr}:{code_section}" e.g. "CA:RTC-17041"
  metadata     — {state, jurisdiction, tax_type, statute_code, section}
"""

from __future__ import annotations

from datetime import datetime

from taxwatch.connectors.base import Connector, DocumentRef, RawDocument
from taxwatch.connectors.http import create_client, fetch_with_retry

# ---------------------------------------------------------------------------
# State adapter registry
# ---------------------------------------------------------------------------

_ADAPTERS: dict[str, type[_StateAdapter]] = {}


def _register(cls):
    _ADAPTERS[cls.state_code] = cls
    return cls


class _StateAdapter:
    state_code: str
    state_name: str

    def __init__(self, state_cfg: dict):
        self.cfg = state_cfg

    def discover(self, client, since: datetime | None) -> list[DocumentRef]:
        raise NotImplementedError

    def fetch(self, ref: DocumentRef, client) -> RawDocument:
        resp = fetch_with_retry(client, ref.url)
        return RawDocument(
            external_id=ref.external_id,
            content=resp.content,
            content_type=resp.headers.get("content-type", "text/html"),
            url=ref.url,
            metadata=ref.metadata,
        )


# ---------------------------------------------------------------------------
# California — Revenue and Taxation Code (RTC)
# leginfo.legislature.ca.gov — statute HTML, section-by-section
# ---------------------------------------------------------------------------


@_register
class _CaliforniaAdapter(_StateAdapter):
    state_code = "CA"
    state_name = "California"

    # Major RTC divisions for income/franchise/sales taxes
    _SECTIONS: list[tuple[str, str, str]] = [
        # (section_no, tax_type, description)
        ("17041", "income_tax", "Personal income tax rates"),
        ("17042", "income_tax", "Income tax rate schedule"),
        ("17072", "income_tax", "Standard deduction"),
        ("23151", "corporate_tax", "Corporation franchise tax rate"),
        ("23153", "corporate_tax", "Corporation income tax rate"),
        ("6051", "sales_tax", "Sales tax rate"),
        ("6201", "sales_tax", "Use tax rate"),
        ("13302", "estate_tax", "Generation-skipping transfer"),
    ]
    _BASE = "https://leginfo.legislature.ca.gov/faces/codes_displaySection.xhtml"

    def discover(self, client, since) -> list[DocumentRef]:
        refs = []
        for sec, tax_type, desc in self._SECTIONS:
            url = f"{self._BASE}?sectionNum={sec}.&lawCode=RTC"
            try:
                resp = fetch_with_retry(client, url, method="HEAD")
                last_mod = _parse_http_date(resp.headers.get("last-modified", ""))
            except Exception:
                last_mod = None

            if since and last_mod and last_mod < since:
                continue

            refs.append(
                DocumentRef(
                    external_id=f"CA:RTC-{sec}",
                    title=f"California RTC § {sec} — {desc}",
                    doc_type="statute",
                    url=url,
                    issued_at=last_mod,
                    metadata={
                        "state": "CA",
                        "state_name": self.state_name,
                        "jurisdiction": "US-CA",
                        "tax_type": tax_type,
                        "statute_code": "RTC",
                        "section": sec,
                    },
                )
            )
        return refs


# ---------------------------------------------------------------------------
# Texas — Texas Tax Code (TTC)
# statutes.capitol.texas.gov — HTML chapters
# ---------------------------------------------------------------------------


@_register
class _TexasAdapter(_StateAdapter):
    state_code = "TX"
    state_name = "Texas"

    # Texas Tax Code chapters (no state income tax; focus on franchise/sales)
    _CHAPTERS: list[tuple[str, str, str]] = [
        ("151", "sales_tax", "Sales and Use Tax Act"),
        ("171", "franchise_tax", "Franchise Tax"),
        ("211", "property_tax", "Property Tax — General Provisions"),
        ("41", "property_tax", "Property Tax — Review of Appraisals"),
        ("113", "admin", "Collection of State Revenue"),
    ]
    _BASE = "https://statutes.capitol.texas.gov/Docs/TX/htm/TX.{chapter}.htm"

    def discover(self, client, since) -> list[DocumentRef]:
        refs = []
        for chap, tax_type, desc in self._CHAPTERS:
            url = self._BASE.format(chapter=chap)
            try:
                resp = fetch_with_retry(client, url, method="HEAD")
                last_mod = _parse_http_date(resp.headers.get("last-modified", ""))
            except Exception:
                last_mod = None

            if since and last_mod and last_mod < since:
                continue

            refs.append(
                DocumentRef(
                    external_id=f"TX:TC-{chap}",
                    title=f"Texas Tax Code Ch. {chap} — {desc}",
                    doc_type="statute",
                    url=url,
                    issued_at=last_mod,
                    metadata={
                        "state": "TX",
                        "state_name": self.state_name,
                        "jurisdiction": "US-TX",
                        "tax_type": tax_type,
                        "statute_code": "TX Tax Code",
                        "chapter": chap,
                    },
                )
            )
        return refs


# ---------------------------------------------------------------------------
# Florida — Florida Statutes
# flsenate.gov — chapter HTML pages
# ---------------------------------------------------------------------------


@_register
class _FloridaAdapter(_StateAdapter):
    state_code = "FL"
    state_name = "Florida"

    _CHAPTERS: list[tuple[str, str, str]] = [
        ("198", "estate_tax", "Estate Tax"),
        ("201", "doc_stamp", "Excise Tax on Documents"),
        ("212", "sales_tax", "Sales and Use Tax"),
        ("220", "income_tax", "Corporate Income Tax"),
        ("624", "ins_premium", "Insurance Premium Tax"),
    ]
    _BASE = "https://www.flsenate.gov/Laws/Statutes/2025/{chapter}"

    def discover(self, client, since) -> list[DocumentRef]:
        refs = []
        for chap, tax_type, desc in self._CHAPTERS:
            url = self._BASE.format(chapter=chap)
            try:
                resp = fetch_with_retry(client, url, method="HEAD")
                last_mod = _parse_http_date(resp.headers.get("last-modified", ""))
            except Exception:
                last_mod = None

            if since and last_mod and last_mod < since:
                continue

            refs.append(
                DocumentRef(
                    external_id=f"FL:FS-{chap}",
                    title=f"Florida Statutes Ch. {chap} — {desc}",
                    doc_type="statute",
                    url=url,
                    issued_at=last_mod,
                    metadata={
                        "state": "FL",
                        "state_name": self.state_name,
                        "jurisdiction": "US-FL",
                        "tax_type": tax_type,
                        "statute_code": "Florida Statutes",
                        "chapter": chap,
                    },
                )
            )
        return refs


# ---------------------------------------------------------------------------
# Washington — WAC Title 458 (Department of Revenue regulations)
# apps.leg.wa.gov — XML API, no key required
# ---------------------------------------------------------------------------


@_register
class _WashingtonAdapter(_StateAdapter):
    state_code = "WA"
    state_name = "Washington"

    # WAC 458 chapters (Washington has no income tax — sales/B&O only)
    _CHAPTERS: list[tuple[str, str, str]] = [
        ("458-20", "sales_tax", "Retail Sales Tax / B&O Tax Rules"),
        ("458-16", "property_tax", "Property Tax Exemptions"),
        ("458-29", "excise_tax", "Real Estate Excise Tax"),
        ("458-40", "timber_tax", "Timber Excise Tax"),
    ]
    _BASE = "https://apps.leg.wa.gov/wac/default.aspx?cite={chapter}"

    def discover(self, client, since) -> list[DocumentRef]:
        refs = []
        for chap, tax_type, desc in self._CHAPTERS:
            url = self._BASE.format(chapter=chap)
            refs.append(
                DocumentRef(
                    external_id=f"WA:WAC-{chap}",
                    title=f"Washington WAC {chap} — {desc}",
                    doc_type="regulation",
                    url=url,
                    issued_at=None,
                    metadata={
                        "state": "WA",
                        "state_name": self.state_name,
                        "jurisdiction": "US-WA",
                        "tax_type": tax_type,
                        "statute_code": "WAC",
                        "chapter": chap,
                    },
                )
            )
        return refs


# ---------------------------------------------------------------------------
# Illinois — Illinois Compiled Statutes (35 ILCS = Revenue)
# ilga.gov — HTML chapter pages
# ---------------------------------------------------------------------------


@_register
class _IllinoisAdapter(_StateAdapter):
    state_code = "IL"
    state_name = "Illinois"

    _ACTS: list[tuple[str, str, str, str]] = [
        # (act_id, chapter_id, tax_type, description)
        ("596", "8", "income_tax", "Illinois Income Tax Act (35 ILCS 5)"),
        ("1825", "8", "sales_tax", "Retailers' Occupation Tax Act (35 ILCS 120)"),
        ("4229", "8", "estate_tax", "Illinois Estate and Generation-Skipping Transfer Tax Act"),
        ("1835", "8", "corporate_tax", "Corporate Franchise Tax Act (35 ILCS 620)"),
    ]
    _BASE = "https://www.ilga.gov/legislation/ilcs/ilcs3.asp?ActID={act_id}&ChapterID={chapter_id}"

    def discover(self, client, since) -> list[DocumentRef]:
        refs = []
        for act_id, chap_id, tax_type, desc in self._ACTS:
            url = self._BASE.format(act_id=act_id, chapter_id=chap_id)
            refs.append(
                DocumentRef(
                    external_id=f"IL:ILCS-{act_id}",
                    title=f"Illinois — {desc}",
                    doc_type="statute",
                    url=url,
                    issued_at=None,
                    metadata={
                        "state": "IL",
                        "state_name": self.state_name,
                        "jurisdiction": "US-IL",
                        "tax_type": tax_type,
                        "statute_code": "35 ILCS",
                        "act_id": act_id,
                    },
                )
            )
        return refs


# ---------------------------------------------------------------------------
# New York — NY Tax Law
# nysenate.gov — requires API key in config as `ny_api_key`
# ---------------------------------------------------------------------------


@_register
class _NewYorkAdapter(_StateAdapter):
    state_code = "NY"
    state_name = "New York"

    _ARTICLES: list[tuple[str, str, str]] = [
        ("22", "income_tax", "Personal Income Tax"),
        ("9-A", "corporate_tax", "Franchise Tax on Business Corporations"),
        ("28", "sales_tax", "Sales and Compensating Use Taxes"),
        ("26", "estate_tax", "Estate Tax"),
        ("33", "ins_premium", "Insurance Corporations Franchise Tax"),
    ]
    _BASE = "https://legislation.nysenate.gov/api/3/laws/TAX/{article}"

    def discover(self, client, since) -> list[DocumentRef]:
        api_key = self.cfg.get("ny_api_key", "")
        if not api_key:
            # Fall back to public HTML
            return self._discover_html(client, since)

        refs = []
        for article, tax_type, desc in self._ARTICLES:
            url = self._BASE.format(article=article)
            try:
                resp = fetch_with_retry(client, url, params={"key": api_key})
                data = resp.json()
                updated_str = data.get("result", {}).get("publishedDateTime") or ""
                issued_at = _parse_iso(updated_str[:10]) if updated_str else None
            except Exception:
                issued_at = None

            refs.append(
                DocumentRef(
                    external_id=f"NY:TAX-Art{article}",
                    title=f"New York Tax Law Article {article} — {desc}",
                    doc_type="statute",
                    url=f"https://www.nysenate.gov/legislation/laws/TAX/article-{article}",
                    issued_at=issued_at,
                    metadata={
                        "state": "NY",
                        "state_name": self.state_name,
                        "jurisdiction": "US-NY",
                        "tax_type": tax_type,
                        "statute_code": "NY Tax Law",
                        "article": article,
                        "api_url": url,
                    },
                )
            )
        return refs

    def _discover_html(self, client, since) -> list[DocumentRef]:
        refs = []
        for article, tax_type, desc in self._ARTICLES:
            url = f"https://www.nysenate.gov/legislation/laws/TAX/article-{article}"
            refs.append(
                DocumentRef(
                    external_id=f"NY:TAX-Art{article}",
                    title=f"New York Tax Law Article {article} — {desc}",
                    doc_type="statute",
                    url=url,
                    issued_at=None,
                    metadata={
                        "state": "NY",
                        "state_name": self.state_name,
                        "jurisdiction": "US-NY",
                        "tax_type": tax_type,
                        "statute_code": "NY Tax Law",
                        "article": article,
                    },
                )
            )
        return refs


# ---------------------------------------------------------------------------
# Main connector
# ---------------------------------------------------------------------------


class UsStateTaxConnector(Connector):
    """Unified US state tax law connector.

    Config example (sources.yaml):
      connector: us_state_tax
      config:
        states:
          - CA
          - TX
          - FL
          - WA
          - NY
          - IL
        ny_api_key: "..."   # optional; falls back to HTML scraping without it
    """

    key = "us_state_tax"
    country = "US"

    def discover(self, since: datetime | None = None) -> list[DocumentRef]:
        configured = self.source_config.get("states", list(_ADAPTERS))
        target_states: list[str] = [s.upper() for s in configured]
        client = create_client()
        refs: list[DocumentRef] = []

        for state in target_states:
            adapter_cls = _ADAPTERS.get(state)
            if not adapter_cls:
                continue
            adapter = adapter_cls(self.source_config)
            try:
                refs.extend(adapter.discover(client, since))
            except Exception:
                continue

        return refs

    def fetch(self, ref: DocumentRef) -> RawDocument:
        state = ref.metadata.get("state", "")
        adapter_cls = _ADAPTERS.get(state)
        client = create_client()
        if adapter_cls:
            adapter = adapter_cls(self.source_config)
            return adapter.fetch(ref, client)
        # Generic fallback
        resp = fetch_with_retry(client, ref.url)
        return RawDocument(
            external_id=ref.external_id,
            content=resp.content,
            content_type=resp.headers.get("content-type", "text/html"),
            url=ref.url,
            metadata=ref.metadata,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_http_date(s: str) -> datetime | None:
    if not s:
        return None
    import email.utils

    try:
        return email.utils.parsedate_to_datetime(s).replace(tzinfo=None)
    except Exception:
        return None


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None
