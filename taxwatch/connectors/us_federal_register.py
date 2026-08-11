"""Federal Register API connector (IRS documents)."""

from __future__ import annotations

from datetime import datetime

from taxwatch.connectors.base import Connector, DocumentRef, RawDocument
from taxwatch.connectors.http import create_client, fetch_with_retry


class UsFederalRegisterConnector(Connector):
    key = "us_federal_register"
    country = "US"

    def _base_url(self) -> str:
        return self.source_config.get("base_url", "https://www.federalregister.gov/api/v1")

    def discover(self, since: datetime | None = None) -> list[DocumentRef]:
        client = create_client()
        params: dict = {
            "conditions[agencies][]": self.source_config.get("agency", "internal-revenue-service"),
            "per_page": 50,
            "order": "newest",
            "fields[]": ["title", "publication_date", "document_number", "html_url", "type"],
        }
        if since:
            params["conditions[publication_date][gte]"] = since.strftime("%Y-%m-%d")

        resp = fetch_with_retry(client, f"{self._base_url()}/documents.json", params=params)
        data = resp.json()
        refs: list[DocumentRef] = []

        for doc in data.get("results", []):
            doc_type = _map_fr_type(doc.get("type", ""))
            refs.append(
                DocumentRef(
                    external_id=doc["document_number"],
                    title=doc.get("title", ""),
                    doc_type=doc_type,
                    url=doc.get("html_url", ""),
                    issued_at=_parse_iso_date(doc.get("publication_date")),
                )
            )

        return refs

    def fetch(self, ref: DocumentRef) -> RawDocument:
        client = create_client()
        resp = fetch_with_retry(
            client,
            f"{self._base_url()}/documents/{ref.external_id}.json",
            params={
                "fields[]": [
                    "title",
                    "abstract",
                    "body_html_url",
                    "full_text_xml_url",
                    "raw_text_url",
                ]
            },
        )
        return RawDocument(
            external_id=ref.external_id,
            content=resp.content,
            content_type="application/json",
            url=ref.url,
            metadata=ref.metadata,
        )


def _map_fr_type(fr_type: str) -> str:
    mapping = {
        "Rule": "regulation",
        "Proposed Rule": "regulation",
        "Notice": "announcement",
        "Presidential Document": "statute",
    }
    return mapping.get(fr_type, "announcement")


def _parse_iso_date(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None
