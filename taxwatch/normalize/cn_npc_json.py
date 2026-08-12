"""Normalizer for NPC (flk.npc.gov.cn) statute JSON.

The cn_npc connector stores content as JSON wrapping the article HTML::

    {"title": "中华人民共和国增值税法", "body": "<p>第一条 ...</p>", "meta": {...}}

This normalizer unwraps the JSON and delegates the HTML body to the same
article-splitting logic that handles chinatax documents.
"""

from __future__ import annotations

import json

from taxwatch.connectors.base import RawDocument
from taxwatch.normalize.base import NormalizedDoc, Normalizer
from taxwatch.normalize.cn_tax_html import CnTaxHtmlNormalizer


class CnNpcJsonNormalizer(Normalizer):
    def __init__(self) -> None:
        self._html_normalizer = CnTaxHtmlNormalizer()

    def normalize(self, raw: RawDocument) -> NormalizedDoc:
        payload = json.loads(raw.content.decode("utf-8"))
        title = (payload.get("title") or "").strip()
        body = payload.get("body") or ""

        html_raw = RawDocument(
            external_id=raw.external_id,
            content=body.encode("utf-8"),
            content_type="text/html",
            url=raw.url,
            metadata={**raw.metadata, "title": title},
        )
        result = self._html_normalizer.normalize(html_raw)

        if title and not result.title:
            result = NormalizedDoc(
                external_id=result.external_id,
                title=title,
                provisions=result.provisions,
                metadata={**result.metadata, "source_format": "cn_npc_json"},
            )

        return result
