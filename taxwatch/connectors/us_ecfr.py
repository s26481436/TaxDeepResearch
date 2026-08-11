"""eCFR (Electronic Code of Federal Regulations) connector — Title 26 (Internal Revenue).

Public API, no key required:
  GET https://www.ecfr.gov/api/versioner/v1/versions/title-26
      → full amendment history, each entry has date + section identifier + substantive flag
  GET https://www.ecfr.gov/api/versioner/v1/full/{date}/title-26.xml?part={part}
      → full text of one CFR part as XML on a given date

Strategy:
  discover() — call the versions endpoint to list all section-level amendments,
                filtered by `since` and by the parts listed in config.
  fetch()    — download the full-text XML for the relevant part and embed it.

Key parts of Title 26 (IRS):
  Part 1   — Income Taxes (Subchapter A)
  Part 20  — Estate Tax
  Part 25  — Gift Tax
  Part 31  — Employment Taxes
  Part 301 — Procedure and Administration
"""
from __future__ import annotations

import re
from datetime import datetime

from taxwatch.connectors.base import Connector, DocumentRef, RawDocument
from taxwatch.connectors.http import create_client, fetch_with_retry

_VERSIONS_URL = "https://www.ecfr.gov/api/versioner/v1/versions/title-26"
_FULL_TEXT_URL = "https://www.ecfr.gov/api/versioner/v1/full/{date}/title-26.xml"

# Default parts to track if none configured
_DEFAULT_PARTS = ["1", "20", "25", "31", "301"]


class UsEcfrConnector(Connector):
    key = "us_ecfr"
    country = "US"

    def _target_parts(self) -> set[str]:
        return set(str(p) for p in self.source_config.get("parts", _DEFAULT_PARTS))

    def discover(self, since: datetime | None = None) -> list[DocumentRef]:
        client = create_client()
        resp = fetch_with_retry(client, _VERSIONS_URL)
        all_versions: list[dict] = resp.json().get("content_versions", [])

        target_parts = self._target_parts()
        seen: dict[str, DocumentRef] = {}  # section_id → latest ref

        for v in all_versions:
            if not v.get("substantive", True):
                continue
            part = str(v.get("part", ""))
            if part not in target_parts:
                continue
            amended_on = _parse_iso(v.get("date", ""))
            if since and amended_on and amended_on < since:
                continue

            section_id = v.get("identifier", "")
            section_name = v.get("name", section_id)
            # Use part-level as the document unit (sections roll up into parts)
            doc_id = f"26-CFR-{part}"
            title = f"26 CFR Part {part}"

            if doc_id not in seen or (amended_on and (seen[doc_id].issued_at or datetime.min) < amended_on):
                seen[doc_id] = DocumentRef(
                    external_id=doc_id,
                    title=title,
                    doc_type="regulation",
                    url=f"https://www.ecfr.gov/current/title-26/part-{part}",
                    issued_at=amended_on,
                    metadata={
                        "title": "26",
                        "part": part,
                        "latest_section": section_id,
                        "latest_section_name": section_name,
                        "jurisdiction": "US-federal",
                    },
                )

        return list(seen.values())

    def fetch(self, ref: DocumentRef) -> RawDocument:
        part = ref.metadata.get("part", "")
        date_str = (ref.issued_at or datetime.utcnow()).strftime("%Y-%m-%d")
        url = _FULL_TEXT_URL.format(date=date_str)
        client = create_client()
        resp = fetch_with_retry(client, url, params={"part": part})
        return RawDocument(
            external_id=ref.external_id,
            content=resp.content,
            content_type="application/xml",
            url=ref.url,
            metadata=ref.metadata,
        )


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None
