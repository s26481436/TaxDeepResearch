"""国家法律法规数据库 connector (flk.npc.gov.cn).

Fetches the authoritative full text of primary PRC tax statutes enacted by
the National People's Congress (全国人大) and its Standing Committee.  These
are the *本法* — e.g. 中华人民共和国增值税法, 中华人民共和国企业所得税法 — as
opposed to the implementing regulations, notices and announcements published
by subordinate agencies.

The ``flk.npc.gov.cn`` site exposes a JSON search API::

    POST https://flk.npc.gov.cn/api/
         type=flfg&searchType=title&title=<keyword>&...

and a detail endpoint::

    POST https://flk.npc.gov.cn/api/detail
         id=<zlsxid>

Both return ``application/json``.  The detail payload includes the article-
by-article HTML body in the ``result.body`` field.

Configuration (sources.yaml)::

    cn-npc:
      connector: cn_npc
      config:
        keywords:            # Title keywords to search for (one query per keyword)
          - 增值税法
          - 企业所得税法
          - ...
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from taxwatch.connectors.base import Connector, DocumentRef, RawDocument
from taxwatch.connectors.http import create_client

_SEARCH_API = "https://flk.npc.gov.cn/api/"
_DETAIL_API = "https://flk.npc.gov.cn/api/detail"

_REFERER = "https://flk.npc.gov.cn/index.html"

_DEFAULT_KEYWORDS = [
    "增值税法",
    "企业所得税法",
    "个人所得税法",
    "税收征收管理法",
    "印花税法",
    "契税法",
    "城市维护建设税法",
    "车船税法",
    "环境保护税法",
    "资源税法",
    "耕地占用税法",
    "烟叶税法",
    "船舶吨税法",
    "车辆购置税法",
]


class CnNpcConnector(Connector):
    key = "cn_npc"
    country = "CN"

    def _client(self):
        return create_client(
            timeout=60,
            headers={
                "Referer": _REFERER,
                "Origin": "https://flk.npc.gov.cn",
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            },
        )

    def discover(self, since: datetime | None = None) -> list[DocumentRef]:
        keywords = self.source_config.get("keywords") or _DEFAULT_KEYWORDS
        client = self._client()
        refs: list[DocumentRef] = []
        seen: set[str] = set()

        for keyword in keywords:
            try:
                entries = self._search(client, keyword)
            except Exception:
                continue

            for entry in entries:
                ref = self._to_ref(entry)
                if ref is None or ref.external_id in seen:
                    continue
                if since and ref.issued_at and ref.issued_at < since:
                    continue
                seen.add(ref.external_id)
                refs.append(ref)

        return refs

    def _search(self, client, keyword: str) -> list[dict[str, Any]]:
        data = {
            "type": "flfg",
            "searchType": "title",
            "title": keyword,
            "sortTr": "f_bbrq_s",
            "gbrqStart": "",
            "gbrqEnd": "",
            "sxrqStart": "",
            "sxrqEnd": "",
            "sort": "true",
            "page": "1",
            "size": "10",
        }
        resp = client.post(_SEARCH_API, data=data)
        resp.raise_for_status()
        payload = resp.json()
        result = payload.get("result") or {}
        return result.get("data") or []

    def _to_ref(self, entry: dict[str, Any]) -> DocumentRef | None:
        title = (entry.get("title") or "").strip()
        zlsxid = (entry.get("id") or "").strip()
        if not title or not zlsxid:
            return None

        title = _strip_html(title)
        publish_date = entry.get("publish") or entry.get("expiry") or ""
        issued_at = _parse_date(publish_date)

        return DocumentRef(
            external_id=f"npc:{zlsxid}",
            title=title,
            doc_type="statute",
            url=f"https://flk.npc.gov.cn/detail2.html?ZmY={zlsxid}",
            issued_at=issued_at,
            metadata={
                "zlsxid": zlsxid,
                "title": title,
                "office": entry.get("office") or "",
                "expiry": entry.get("expiry") or "",
                "status": entry.get("status") or "",
            },
        )

    def fetch(self, ref: DocumentRef) -> RawDocument:
        client = self._client()
        zlsxid = ref.metadata.get("zlsxid", "")
        if not zlsxid:
            zlsxid = ref.external_id.removeprefix("npc:")

        resp = client.post(_DETAIL_API, data={"id": zlsxid})
        resp.raise_for_status()
        payload = resp.json()
        result = payload.get("result") or {}
        body = result.get("body") or ""

        import json

        content = json.dumps(
            {"title": ref.title, "body": body, "meta": result},
            ensure_ascii=False,
        ).encode("utf-8")

        return RawDocument(
            external_id=ref.external_id,
            content=content,
            content_type="application/json",
            url=ref.url,
            metadata=ref.metadata,
        )


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def _parse_date(raw: str) -> datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y.%m.%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(raw[:len(fmt) + 4].strip(), fmt)
        except ValueError:
            continue
    return None
