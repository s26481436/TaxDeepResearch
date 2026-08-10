"""Normalizer for 全國法規資料庫 JSON API response.

The API returns JSON with structure like:
{
  "LawName": "所得稅法",
  "LawArticles": [
    {"ArticleNo": "第 1 條", "ArticleContent": "..."},
    {"ArticleNo": "第 2 條", "ArticleContent": "..."},
  ]
}
"""
from __future__ import annotations

import json
import re

from taxwatch.connectors.base import RawDocument
from taxwatch.normalize.base import NormalizedDoc, Normalizer, ProvisionData
from taxwatch.normalize.text import normalize_text


class TwLawJsonNormalizer(Normalizer):
    def normalize(self, raw: RawDocument) -> NormalizedDoc:
        data = json.loads(raw.content)
        law_name = data.get("LawName", raw.external_id)
        articles = data.get("LawArticles", [])

        provisions: list[ProvisionData] = []
        for art in articles:
            article_no = art.get("ArticleNo", "").strip()
            content = art.get("ArticleContent", "").strip()
            if not article_no and not content:
                continue

            node_key = _build_node_key(law_name, article_no)
            normalized_content = normalize_text(content)

            provisions.append(ProvisionData(
                node_key=node_key,
                heading=article_no,
                text=normalized_content,
            ))

        return NormalizedDoc(
            external_id=raw.external_id,
            title=law_name,
            provisions=provisions,
            metadata={"source_format": "tw_law_json"},
        )


def _build_node_key(law_name: str, article_no: str) -> str:
    """Build stable node key like '所得稅法#14' from '第 14 條'."""
    m = re.search(r"第\s*(\d+(?:-\d+)?)\s*條", article_no)
    if m:
        return f"{law_name}#{m.group(1)}"
    cleaned = re.sub(r"\s+", "", article_no)
    return f"{law_name}#{cleaned}" if cleaned else law_name
