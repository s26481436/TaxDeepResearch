#!/usr/bin/env python3
"""Migration script for Phase 1: split taxonomy keys with country prefixes.

Idempotent migration:
1. `tax_requirements.tax_key`: old -> f"{country.lower()}_{old}"
   (e.g., vat with country CN -> cn_vat; with country TW -> tw_business_tax or tw_vat / tw_{old})
   Note: if key already starts with {country.lower()}_, it is left untouched.
2. `corpus_documents.tax_keys`: JSON list of strings -> prefixed with cn_ if from chinatax / CN.
   (e.g., ["vat"] -> ["cn_vat"])
"""

from __future__ import annotations

import logging
from sqlalchemy.orm import Session

from taxwatch.db import get_session, init_db
from taxwatch.models import CorpusDocument, TaxRequirement
from taxwatch.taxonomy import by_key

logger = logging.getLogger(__name__)

# Explicit mapping for legacy keys if a direct country prefix needs renaming:
_LEGACY_TAX_KEY_MAP: dict[tuple[str, str], str] = {
    ("CN", "vat"): "cn_vat",
    ("CN", "enterprise_income"): "cn_enterprise_income",
    ("CN", "individual_income"): "cn_individual_income",
    ("CN", "income"): "cn_income",
    ("CN", "property"): "cn_property",
    ("CN", "stamp"): "cn_stamp",
    ("CN", "environmental"): "cn_environmental",
    ("CN", "resource"): "cn_resource",
    ("CN", "urban_maintenance"): "cn_urban_maintenance",
    ("CN", "consumption"): "cn_consumption",
    ("CN", "estate_gift"): "cn_estate_gift",
    ("CN", "vehicle"): "cn_vehicle",
    ("CN", "tobacco_alcohol"): "cn_tobacco",
    ("CN", "securities"): "cn_securities",
    ("CN", "customs"): "cn_customs",
    ("CN", "collection"): "cn_collection",
    ("CN", "other"): "cn_other",

    ("TW", "vat"): "tw_business_tax",
    ("TW", "business_tax"): "tw_business_tax",
    ("TW", "enterprise_income"): "tw_profit_seeking",
    ("TW", "profit_seeking"): "tw_profit_seeking",
    ("TW", "individual_income"): "tw_individual_income",
    ("TW", "income"): "tw_income",
    ("TW", "property"): "tw_property",
    ("TW", "stamp"): "tw_stamp",
    ("TW", "consumption"): "tw_commodity",
    ("TW", "commodity"): "tw_commodity",
    ("TW", "estate_gift"): "tw_estate_gift",
    ("TW", "vehicle"): "tw_vehicle",
    ("TW", "tobacco_alcohol"): "tw_tobacco_alcohol",
    ("TW", "securities"): "tw_securities",
    ("TW", "customs"): "tw_customs",
    ("TW", "collection"): "tw_collection",
    ("TW", "other"): "tw_other",
}


def migrate_tax_keys(session: Session) -> dict[str, int]:
    req_migrated = 0
    corpus_migrated = 0

    # 1. Migrate TaxRequirement
    requirements = session.query(TaxRequirement).all()
    for req in requirements:
        country = (req.country or "CN").upper()
        prefix = f"{country.lower()}_"
        old_key = req.tax_key

        if old_key.startswith(prefix):
            continue

        new_key = _LEGACY_TAX_KEY_MAP.get((country, old_key))
        if not new_key:
            new_key = f"{prefix}{old_key}"

        req.tax_key = new_key
        req_migrated += 1

    # 2. Migrate CorpusDocument
    corpus_docs = session.query(CorpusDocument).all()
    for doc in corpus_docs:
        if not doc.tax_keys:
            continue
        # chinatax corpus is CN
        country = "CN" if (not doc.corpus_key or "china" in doc.corpus_key.lower()) else "CN"
        prefix = f"{country.lower()}_"

        new_keys: list[str] = []
        changed = False
        for k in doc.tax_keys:
            if k.startswith(prefix):
                new_keys.append(k)
            else:
                mapped = _LEGACY_TAX_KEY_MAP.get((country, k), f"{prefix}{k}")
                new_keys.append(mapped)
                changed = True

        if changed:
            doc.tax_keys = new_keys
            corpus_migrated += 1

    session.commit()
    return {"requirements_updated": req_migrated, "corpus_documents_updated": corpus_migrated}


def main() -> None:
    init_db()
    session = get_session()
    try:
        stats = migrate_tax_keys(session)
        print(f"Migration completed: {stats}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
