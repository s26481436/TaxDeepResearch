"""Import a reference corpus from Parquet into `corpus_documents`.

Expected columns (the chinatax policy corpus layout):
    title, channel, content, document_number, effect_level, tax_type,
    aging, labels, issuing_department, written_date, url

Only `title` is strictly required; everything else degrades to empty.

Licence note: corpora are imported into the local database and never
redistributed by TaxWatch. Check the source corpus's licence before using it
for anything beyond internal analysis — the chinatax policy corpus is
CC-BY-NC-4.0, i.e. non-commercial.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from taxwatch.models import CorpusDocument
from taxwatch.taxonomy import classify
from taxwatch.wenhao import extract_first, normalize

logger = logging.getLogger(__name__)

REQUIRED_COLUMN = "title"

# 「税收政策-增值税,税费征管」 → the corpus's own tax vocabulary.
_TAX_LABEL_MAP: dict[str, str] = {
    "增值税": "vat",
    "营业税": "vat",
    "企业所得税": "enterprise_income",
    "个人所得税": "individual_income",
    "印花税": "stamp",
    "环境保护税": "environmental",
    "资源税": "resource",
    "城市维护建设税": "urban_maintenance",
    "消费税": "consumption",
    "契税": "property",
    "房产税": "property",
    "土地增值税": "property",
    "城镇土地使用税": "property",
    "耕地占用税": "property",
    "车辆购置税": "vehicle",
    "车船税": "vehicle",
    "烟叶税": "tobacco_alcohol",
    "关税": "customs",
    "进出口税收": "customs",
    "税费征管": "collection",
}


def parse_tax_keys(raw: str) -> list[str]:
    """Map the corpus's comma-separated tax labels onto our taxonomy keys.

    `税收政策-增值税,税费征管` → `["vat", "collection"]`. Unknown labels are
    dropped rather than guessed at.
    """
    keys: list[str] = []
    for part in (raw or "").split(","):
        label = part.strip().split("-")[-1].strip()
        key = _TAX_LABEL_MAP.get(label)
        if key and key not in keys:
            keys.append(key)
    return keys


def read_parquet(path: Path) -> Iterator[dict[str, Any]]:
    """Stream rows from a Parquet file as plain dicts."""
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - depends on optional extra
        raise RuntimeError(
            "Reading a Parquet corpus needs pyarrow. Install with: "
            'pip install "taxwatch[corpus]"'
        ) from exc

    parquet = pq.ParquetFile(str(path))
    if REQUIRED_COLUMN not in parquet.schema_arrow.names:
        raise ValueError(
            f"Corpus is missing the required '{REQUIRED_COLUMN}' column. "
            f"Found: {parquet.schema_arrow.names}"
        )

    for batch in parquet.iter_batches(batch_size=500):
        yield from batch.to_pylist()


def import_corpus(
    session: Session,
    path: Path,
    *,
    corpus_key: str = "chinatax",
    corpus_version: str = "",
    base_url: str = "https://fgk.chinatax.gov.cn",
    replace: bool = True,
) -> dict[str, int]:
    """Load a Parquet corpus into the database.

    Returns counts for the import: rows read, stored, and how many carry a
    usable 文號 (the join key against our own documents).
    """
    if replace:
        deleted = (
            session.query(CorpusDocument)
            .filter_by(corpus_key=corpus_key)
            .delete(synchronize_session=False)
        )
        logger.info("Replacing corpus %r: removed %d existing rows", corpus_key, deleted)

    read = stored = with_wenhao = 0
    for row in read_parquet(path):
        read += 1
        doc = _build(row, corpus_key, corpus_version, base_url)
        if doc is None:
            continue
        session.add(doc)
        stored += 1
        if doc.document_number:
            with_wenhao += 1
        if stored % 500 == 0:
            session.flush()

    session.commit()
    stats = {"read": read, "stored": stored, "with_document_number": with_wenhao}
    logger.info("Imported corpus %r: %s", corpus_key, stats)
    return stats


def _build(
    row: dict[str, Any],
    corpus_key: str,
    corpus_version: str,
    base_url: str,
) -> CorpusDocument | None:
    title = _text(row.get("title"))
    if not title:
        return None

    raw_number = _text(row.get("document_number"))
    # Prefer the corpus's own field; fall back to parsing it out of the title
    # so rows with an empty 文號 column can still be looked up.
    document_number = normalize(raw_number) if raw_number else (extract_first(title) or "")

    tax_keys = parse_tax_keys(_text(row.get("tax_type")))
    if not tax_keys:
        inferred = classify(title)
        if inferred.key != "other":
            tax_keys = [inferred.key]

    return CorpusDocument(
        corpus_key=corpus_key,
        corpus_version=corpus_version,
        document_number=document_number,
        title=title,
        channel=_text(row.get("channel")),
        effect_level=_text(row.get("effect_level")),
        tax_type_raw=_text(row.get("tax_type")),
        tax_keys=tax_keys,
        aging=_text(row.get("aging")),
        labels=_text(row.get("labels")),
        issuing_department=_text(row.get("issuing_department")),
        written_date=_date(_text(row.get("written_date"))),
        url=_absolute_url(_text(row.get("url")), base_url),
        content=_text(row.get("content")),
    )


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("﻿", "").strip()


def _date(value: str) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value[:19], fmt)
        except ValueError:
            continue
    return None


def _absolute_url(url: str, base_url: str) -> str:
    if not url:
        return ""
    if url.startswith("http"):
        return url
    return f"{base_url.rstrip('/')}/{url.lstrip('/')}"
