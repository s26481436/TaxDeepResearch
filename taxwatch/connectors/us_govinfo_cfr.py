"""govinfo.gov bulk CFR connector — Title 26, annual print edition.

govinfo.gov publishes annual CFR volumes as XML without requiring an API key:
  https://www.govinfo.gov/bulkdata/CFR/{year}/title-26/CFR-{year}-title26-vol{n}.xml

Title 26 (2025 edition) has 22 volumes covering:
  vol1–16   Part 1 (Income Tax) — § 1.0-1 through § 1.9999
  vol17      Parts 5–29 (Estate, Gift, Employment taxes)
  vol18–19   Part 31 (Employment Taxes)
  vol20      Parts 40–49 (Excise Taxes)
  vol21–22   Parts 301–602 (Procedure, Admin, Alcohol/Tobacco)

This connector is primarily used for:
  1. Annual snapshot — compare year-over-year editions of Title 26
  2. Fallback full-text when eCFR doesn't have historical XML

discover() — returns one DocumentRef per volume that exists for the configured year.
fetch()    — downloads the XML volume.

Note: for real-time amendment tracking, prefer us_ecfr which tracks individual
section amendments as they happen. This connector is best for annual bulk ingestion.
"""

from __future__ import annotations

from datetime import datetime

from taxwatch.connectors.base import Connector, DocumentRef, RawDocument
from taxwatch.connectors.http import create_client, fetch_with_retry

_BULK_URL = (
    "https://www.govinfo.gov/bulkdata/CFR/{year}/title-{title}/CFR-{year}-title{title}-vol{vol}.xml"
)
_DEFAULT_YEAR = "2025"
_DEFAULT_TITLE = "26"
_MAX_VOL_PROBE = 30  # probe up to this volume number


class UsGovinfoConnector(Connector):
    key = "us_govinfo_cfr"
    country = "US"

    def _year(self) -> str:
        return str(self.source_config.get("year", _DEFAULT_YEAR))

    def _title(self) -> str:
        return str(self.source_config.get("cfr_title", _DEFAULT_TITLE))

    def discover(self, since: datetime | None = None) -> list[DocumentRef]:
        year = self._year()
        title = self._title()
        client = create_client()
        refs: list[DocumentRef] = []

        for vol in range(1, _MAX_VOL_PROBE + 1):
            url = _BULK_URL.format(year=year, title=title, vol=vol)
            try:
                resp = fetch_with_retry(client, url, method="HEAD")
            except Exception:
                break  # gap or end of volumes — stop probing

            if resp.status_code == 404:
                break

            last_modified_str = resp.headers.get("last-modified", "")
            issued_at = _parse_http_date(last_modified_str)

            refs.append(
                DocumentRef(
                    external_id=f"CFR-{year}-title{title}-vol{vol}",
                    title=f"{year} CFR Title {title} Vol. {vol}",
                    doc_type="regulation",
                    url=url,
                    issued_at=issued_at,
                    metadata={
                        "year": year,
                        "cfr_title": title,
                        "volume": vol,
                        "jurisdiction": "US-federal",
                        "download_url": url,
                    },
                )
            )

        return refs

    def fetch(self, ref: DocumentRef) -> RawDocument:
        url = ref.metadata.get("download_url", ref.url)
        client = create_client()
        resp = fetch_with_retry(client, url)
        return RawDocument(
            external_id=ref.external_id,
            content=resp.content,
            content_type="application/xml",
            url=ref.url,
            metadata=ref.metadata,
        )


def _parse_http_date(s: str) -> datetime | None:
    if not s:
        return None
    import email.utils

    try:
        t = email.utils.parsedate_to_datetime(s)
        return t.replace(tzinfo=None)
    except Exception:
        return None
