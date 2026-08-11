"""Normalizer for 全國法規資料庫 batch JSON.

The connector embeds one law's JSON payload (extracted from the batch ZIP).
Actual structure (validated against live API 2026-08-11):

  {
    "LawName": "所得稅法",
    "LawModifiedDate": "20251226",
    "LawAbandonNote": "",
    "LawHistories": "1. 中華民國三十四年...",
    "LawArticles": [
      {"ArticleType": "C", "ArticleNo": "",       "ArticleContent": "第 一 章 總則"},
      {"ArticleType": "A", "ArticleNo": "第 1 條", "ArticleContent": "..."},
    ]
  }

ArticleType:
  "A" = 實質條文（要解析的）
  "C" = 章節標題（ArticleNo 為空，當作 section heading）
"""
from __future__ import annotations

import json
import re

from taxwatch.connectors.base import RawDocument
from taxwatch.normalize.base import NormalizedDoc, Normalizer, ProvisionData
from taxwatch.normalize.text import normalize_text


class TwLawJsonNormalizer(Normalizer):
    def normalize(self, raw: RawDocument) -> NormalizedDoc:
        data = json.loads(raw.content.decode("utf-8") if isinstance(raw.content, bytes) else raw.content)
        law_name = data.get("LawName", raw.external_id)
        abandoned = bool(data.get("LawAbandonNote", "").strip())
        histories = data.get("LawHistories", "")

        provisions: list[ProvisionData] = []
        current_chapter = ""

        for art in data.get("LawArticles", []):
            art_type = art.get("ArticleType", "A")
            article_no = art.get("ArticleNo", "").strip()
            content = art.get("ArticleContent", "").strip()

            if art_type == "C":
                # Chapter/section heading — track for context, don't add as provision
                current_chapter = normalize_text(content)
                continue

            if not content:
                continue

            node_key = _build_node_key(law_name, article_no)
            provisions.append(ProvisionData(
                node_key=node_key,
                heading=article_no,
                text=normalize_text(content),
            ))

        meta: dict = {
            "source_format": "tw_law_json",
            "abandoned": abandoned,
        }
        if histories:
            meta["histories"] = histories[:2000]  # cap to avoid huge rows

        return NormalizedDoc(
            external_id=raw.external_id,
            title=law_name,
            provisions=provisions,
            metadata=meta,
        )


def _build_node_key(law_name: str, article_no: str) -> str:
    """Build stable node key like '所得稅法#14' from '第 14 條'."""
    m = re.search(r"第\s*(\d+(?:-\d+)?)\s*條", article_no)
    if m:
        return f"{law_name}#{m.group(1)}"
    cleaned = re.sub(r"\s+", "", article_no)
    return f"{law_name}#{cleaned}" if cleaned else law_name
