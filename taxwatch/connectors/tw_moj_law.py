"""全國法規資料庫 connector.

law.moj.gov.tw 提供開放資料 XML/JSON，包含法規條文與沿革。
此 connector 抓取稅務相關法律的最新全文。
"""
from __future__ import annotations

from datetime import datetime

from taxwatch.connectors.base import Connector, DocumentRef, RawDocument
from taxwatch.connectors.http import create_client, fetch_with_retry


class TwMojLawConnector(Connector):
    key = "tw_moj_law"
    country = "TW"

    OPEN_DATA_BASE = "https://law.moj.gov.tw/api/LawData"

    def discover(self, since: datetime | None = None) -> list[DocumentRef]:
        client = create_client()
        categories = self.source_config.get("law_categories", [])
        refs: list[DocumentRef] = []

        for pcode in categories:
            try:
                resp = fetch_with_retry(
                    client,
                    f"{self.OPEN_DATA_BASE}/LawInfo",
                    params={"pcode": pcode, "format": "json"},
                )
                data = resp.json()
                law_name = data.get("LawName", pcode)
                modified = data.get("LawModifiedDate", "")

                refs.append(DocumentRef(
                    external_id=pcode,
                    title=law_name,
                    doc_type="statute",
                    url=f"https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode={pcode}",
                    issued_at=_parse_roc_date(modified),
                    metadata={"pcode": pcode},
                ))
            except Exception:
                refs.append(DocumentRef(
                    external_id=pcode,
                    title=pcode,
                    doc_type="statute",
                    url=f"https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode={pcode}",
                ))
        return refs

    def fetch(self, ref: DocumentRef) -> RawDocument:
        client = create_client()
        pcode = ref.metadata.get("pcode", ref.external_id)
        resp = fetch_with_retry(
            client,
            f"{self.OPEN_DATA_BASE}/LawAllArticle",
            params={"pcode": pcode, "format": "json"},
        )
        return RawDocument(
            external_id=ref.external_id,
            content=resp.content,
            content_type="application/json",
            url=ref.url,
            metadata=ref.metadata,
        )


def _parse_roc_date(s: str) -> datetime | None:
    """Parse ROC-era date like '民國 113 年 01 月 03 日'."""
    if not s:
        return None
    import re
    m = re.search(r"(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日", s)
    if not m:
        return None
    year = int(m.group(1)) + 1911
    month = int(m.group(2))
    day = int(m.group(3))
    try:
        return datetime(year, month, day)
    except ValueError:
        return None
