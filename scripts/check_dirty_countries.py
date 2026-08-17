#!/usr/bin/env python3
"""Check for mismatched country between TaxRequirement and Document.source.

Lists any TaxRequirement whose country != Document.source.country.
Does NOT modify any data.
"""

from __future__ import annotations

from taxwatch.db import get_session, init_db
from taxwatch.models import Document, Source, TaxRequirement


def check_mismatched_countries() -> None:
    init_db()
    session = get_session()
    try:
        query = (
            session.query(TaxRequirement, Document, Source)
            .join(Document, TaxRequirement.source_document_id == Document.id)
            .join(Source, Document.source_id == Source.id)
            .filter(TaxRequirement.country != Source.country)
        )
        mismatches = query.all()
        if not mismatches:
            print("✓ 沒有發現 TaxRequirement 與 Source 轄區不一致的資料。")
            return

        print(f"⚠ 發現 {len(mismatches)} 筆 TaxRequirement 轄區不一致：")
        print("-" * 80)
        for req, doc, src in mismatches:
            print(
                f"ID: {req.id:<4} | Req Country: {req.country:<4} | "
                f"Source Country: {src.country:<4} | "
                f"Tax: {req.tax_key:<15} | Scenario: {req.scenario} | Law: {doc.title}"
            )
        print("-" * 80)
    finally:
        session.close()


if __name__ == "__main__":
    check_mismatched_countries()
