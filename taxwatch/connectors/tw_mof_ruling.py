"""財政部解釋函令 connector.

抓取財政部主管法規查詢系統（law-out.mof.gov.tw）的解釋函令。
"""
from __future__ import annotations

from datetime import datetime

from taxwatch.connectors.base import Connector, DocumentRef, RawDocument
from taxwatch.connectors.http import create_client, fetch_with_retry


class TwMofRulingConnector(Connector):
    key = "tw_mof_ruling"
    country = "TW"

    def _base_url(self) -> str:
        return self.source_config.get("base_url", "https://law-out.mof.gov.tw")

    def discover(self, since: datetime | None = None) -> list[DocumentRef]:
        client = create_client()
        base = self._base_url()
        refs: list[DocumentRef] = []

        try:
            resp = fetch_with_retry(
                client,
                f"{base}/LawContent.aspx",
                params={"type": "E", "id": "FL000001"},
            )
            from taxwatch.connectors._tw_html_parser import parse_ruling_list
            refs = parse_ruling_list(resp.text, base)
        except Exception:
            pass

        return refs

    def fetch(self, ref: DocumentRef) -> RawDocument:
        client = create_client()
        url = ref.url or f"{self._base_url()}/LawContent.aspx?id={ref.external_id}"
        resp = fetch_with_retry(client, url)
        return RawDocument(
            external_id=ref.external_id,
            content=resp.content,
            content_type="text/html",
            url=url,
            metadata=ref.metadata,
        )
