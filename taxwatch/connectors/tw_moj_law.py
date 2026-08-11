"""全國法規資料庫 connector.

law.moj.gov.tw Open Data API 提供兩個批次下載端點：
  GET /api/Ch/Law/JSON  → 所有法律（約 1,346 筆）的 ZIP，含條文與沿革
  GET /api/Ch/Order/JSON → 所有命令（約 10,442 筆）的 ZIP，含條文與沿革

回傳格式：ZIP 內含 ChLaw.json / ChOrder.json，結構如下：
  {
    "UpdateDate": "2026/7/31 上午 12:00:00",
    "Laws": [
      {
        "LawLevel": "法律",
        "LawName": "所得稅法",
        "LawURL": "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=G0340003",
        "LawModifiedDate": "20251226",   # YYYYMMDD（西元）
        "LawAbandonNote": "",            # 非空 = 已廢止
        "LawHistories": "...",           # 沿革文字
        "LawArticles": [
          {"ArticleType": "A", "ArticleNo": "第 1 條", "ArticleContent": "..."},
          {"ArticleType": "C", "ArticleNo": "",        "ArticleContent": "第一章 總則"},
        ]
      },
      ...
    ]
  }

ArticleType:
  "A" = 實質條文
  "C" = 章節標題（ArticleNo 為空）
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime

from taxwatch.connectors.base import Connector, DocumentRef, RawDocument
from taxwatch.connectors.http import create_client, fetch_with_retry

_LAW_ENDPOINT = "https://law.moj.gov.tw/api/Ch/Law/JSON"
_ORDER_ENDPOINT = "https://law.moj.gov.tw/api/Ch/Order/JSON"
_PCODE_RE = re.compile(r"pcode=([A-Z0-9]+)")
_YYYYMMDD_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


class TwMojLawConnector(Connector):
    key = "tw_moj_law"
    country = "TW"

    def discover(self, since: datetime | None = None) -> list[DocumentRef]:
        target_pcodes: set[str] = set(self.source_config.get("law_categories", []))
        if not target_pcodes:
            return []

        client = create_client()
        refs: list[DocumentRef] = []

        # Law 端點（法律）和 Order 端點（命令/準則）都要查
        for endpoint in (_LAW_ENDPOINT, _ORDER_ENDPOINT):
            try:
                resp = fetch_with_retry(client, endpoint)
                laws = _parse_zip_response(resp.content)
            except Exception:
                continue

            for law in laws:
                pcode = _extract_pcode(law.get("LawURL", ""))
                if not pcode or pcode not in target_pcodes:
                    continue

                modified = _parse_yyyymmdd(law.get("LawModifiedDate", ""))
                abandoned = bool(law.get("LawAbandonNote", "").strip())
                refs.append(DocumentRef(
                    external_id=pcode,
                    title=law["LawName"],
                    doc_type="statute",
                    url=law.get("LawURL", f"https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode={pcode}"),
                    issued_at=modified,
                    metadata={
                        "pcode": pcode,
                        "abandoned": abandoned,
                        "level": law.get("LawLevel", ""),
                        "endpoint": endpoint,
                        # Embed the full payload so fetch() doesn't need another HTTP call
                        "law_payload": json.dumps(law, ensure_ascii=False),
                    },
                ))

        return refs

    def fetch(self, ref: DocumentRef) -> RawDocument:
        # If discover() already embedded the payload, return it directly.
        if "law_payload" in ref.metadata:
            content = ref.metadata["law_payload"].encode("utf-8")
            return RawDocument(
                external_id=ref.external_id,
                content=content,
                content_type="application/json",
                url=ref.url,
                metadata=ref.metadata,
            )

        # Fallback: re-download the whole batch and extract this pcode.
        pcode = ref.metadata.get("pcode", ref.external_id)
        endpoint = ref.metadata.get("endpoint", _LAW_ENDPOINT)
        client = create_client()
        resp = fetch_with_retry(client, endpoint)
        laws = _parse_zip_response(resp.content)
        for law in laws:
            if _extract_pcode(law.get("LawURL", "")) == pcode:
                return RawDocument(
                    external_id=ref.external_id,
                    content=json.dumps(law, ensure_ascii=False).encode("utf-8"),
                    content_type="application/json",
                    url=ref.url,
                    metadata=ref.metadata,
                )
        raise ValueError(f"pcode {pcode} not found in batch download from {endpoint}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_zip_response(content: bytes) -> list[dict]:
    """Extract the JSON array from the ZIP blob returned by the batch API."""
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        # The ZIP always contains exactly one JSON file (ChLaw.json or ChOrder.json)
        json_name = next(n for n in zf.namelist() if n.endswith(".json"))
        raw = zf.read(json_name).decode("utf-8-sig")
    data = json.loads(raw)
    return data.get("Laws", [])


def _extract_pcode(url: str) -> str | None:
    m = _PCODE_RE.search(url)
    return m.group(1) if m else None


def _parse_yyyymmdd(s: str) -> datetime | None:
    """Parse 'YYYYMMDD' date string used by the MOJ batch API."""
    if not s:
        return None
    m = _YYYYMMDD_RE.match(s.strip())
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _parse_roc_date(s: str) -> datetime | None:
    """Parse ROC-era date like '民國 113 年 01 月 03 日' (kept for backward compat)."""
    if not s:
        return None
    m = re.search(r"(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日", s)
    if not m:
        return None
    year = int(m.group(1)) + 1911
    try:
        return datetime(year, int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
