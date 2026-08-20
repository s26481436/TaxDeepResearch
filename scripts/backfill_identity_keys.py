#!/usr/bin/env python3
"""Backfill dimensions and identity_key for existing TaxRequirement rows.

Idempotent migration script:
- Matches existing TaxRequirement rows against known dimension vocabularies.
- For TW / tw_income: classifies based on scenario and taxpayer_role keywords.
- If dimensions can be determined with certainty, updates `dimensions` and `identity_key`.
- If dimensions cannot be determined, leaves them empty (identity_key = "") to preserve legacy behavior safely.
- Default mode is PREVIEW only. Pass `--yes` to apply changes to the database.

Usage:
    python scripts/backfill_identity_keys.py              # Preview
    python scripts/backfill_identity_keys.py --yes        # Apply changes
    python scripts/backfill_identity_keys.py --country TW --yes
"""

from __future__ import annotations

import argparse
import logging
from typing import Any

from sqlalchemy.orm import Session

from taxwatch.db import get_session, init_db
from taxwatch.models import TaxRequirement
from taxwatch.requirements.dimensions import (
    compute_identity_key,
    get_dimensions_vocabulary,
    validate_dimensions,
)

logger = logging.getLogger(__name__)


def infer_tw_income_dimensions(scenario: str, role: str) -> dict[str, str] | None:
    """Infer (taxpayer_class, tax_scheme, subject_matter, scenario_key) for TW/tw_income.

    Returns dict if all 4 dimensions can be identified, or None if uncertain.
    """
    s = scenario.strip()
    r = role.strip()
    full_text = f"{s} {r}"

    # 1. taxpayer_class
    taxpayer_class = ""
    if "非中華民國境內居住" in full_text or "非居住者" in full_text:
        taxpayer_class = "nonresident_individual"
    elif "居住之個人" in full_text or "居住者" in full_text or "個人" in full_text:
        if "扣繳義務人" in full_text:
            taxpayer_class = "withholding_agent"
        else:
            taxpayer_class = "resident_individual"
    elif "總機構在中華民國境外" in full_text or "境外營利事業" in full_text or "外國營利事業" in full_text:
        taxpayer_class = "foreign_enterprise"
    elif "總機構在中華民國境內" in full_text or "境內總機構" in full_text or "營利事業" in full_text:
        if "獨資" in full_text or "合夥" in full_text:
            taxpayer_class = "sole_proprietorship"
        else:
            taxpayer_class = "domestic_enterprise"
    elif "獨資" in full_text or "合夥" in full_text:
        taxpayer_class = "sole_proprietorship"
    elif "受託人" in full_text:
        taxpayer_class = "trustee"
    elif "受益人" in full_text:
        taxpayer_class = "beneficiary"
    elif "扣繳義務人" in full_text:
        taxpayer_class = "withholding_agent"

    # 2. tax_scheme
    tax_scheme = ""
    if "扣繳" in full_text:
        tax_scheme = "withholding"
    elif "未分配盈餘" in full_text or "盈餘分配" in full_text:
        tax_scheme = "profit_distribution"
    elif "免稅" in full_text or "不計入" in full_text or "不課稅" in full_text:
        tax_scheme = "not_taxable"
    elif "結算" in full_text or "申報" in full_text or "綜合所得稅" in full_text or "營利事業所得稅" in full_text or "營利事業" in full_text or "居住者" in full_text:
        tax_scheme = "annual_filing"

    # 3. subject_matter
    subject_matter = ""
    if "房地" in full_text or "房屋土地" in full_text or "房屋" in full_text or "土地" in full_text:
        subject_matter = "real_estate"
    elif "證券" in full_text or "股票" in full_text:
        subject_matter = "securities"
    elif "期貨" in full_text:
        subject_matter = "futures"
    elif "信託" in full_text:
        subject_matter = "trust_income"
    elif "薪資" in full_text or "利息" in full_text or "股利" in full_text or "扣繳" in full_text:
        subject_matter = "salary_interest" if tax_scheme == "withholding" else "general_income"
    elif "所得" in full_text or "營所稅" in full_text or "綜所稅" in full_text or "營利事業" in full_text or "居住者" in full_text or "個人" in full_text:
        subject_matter = "general_income"

    # 4. scenario_key
    scenario_key = "standard"
    if "105年" in full_text or "房地合一2.0" in full_text or "房地合一" in full_text:
        scenario_key = "post_2016_acquisition"
    elif "預售屋" in full_text or "地上權" in full_text:
        scenario_key = "presale_or_superficies"
    elif "股權" in full_text or "特定條件" in full_text:
        scenario_key = "indirect_shareholding"
    elif "受益人不特定" in full_text or "尚未存在" in full_text:
        scenario_key = "beneficiary_unidentified"
    elif "受益人已確定" in full_text or "特定受益人" in full_text:
        scenario_key = "beneficiary_identified"
    elif "公益信託" in full_text:
        scenario_key = "public_trust"
    elif "會計年度" in full_text:
        scenario_key = "change_of_fiscal_year"
    elif "虧損" in full_text or "互抵" in full_text:
        scenario_key = "loss_carryforward"
    elif "OBU" in full_text or "國際金融業務" in full_text:
        scenario_key = "offshore_banking_unit"

    raw = {
        "taxpayer_class": taxpayer_class,
        "tax_scheme": tax_scheme,
        "subject_matter": subject_matter,
        "scenario_key": scenario_key,
    }
    valid_dims, unknowns, missing = validate_dimensions("TW", "tw_income", raw)
    if not unknowns and not missing and all(valid_dims.values()):
        return valid_dims
    return None


def backfill_identity_keys(
    session: Session,
    *,
    country: str | None = None,
    tax_key: str | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    query = session.query(TaxRequirement)
    if country:
        query = query.filter(TaxRequirement.country == country.upper())
    if tax_key:
        query = query.filter(TaxRequirement.tax_key == tax_key)

    total = 0
    updated = 0
    skipped_already_set = 0
    skipped_undetermined = 0
    results: list[dict[str, Any]] = []

    for req in query.all():
        total += 1
        if req.identity_key:
            skipped_already_set += 1
            continue

        dims = None
        if req.country == "TW" and req.tax_key == "tw_income":
            dims = infer_tw_income_dimensions(req.scenario, req.taxpayer_role)

        if dims:
            id_key = compute_identity_key(dims)
            if id_key:
                if not dry_run:
                    req.dimensions = dims
                    req.identity_key = id_key
                updated += 1
                results.append(
                    {
                        "id": req.id,
                        "scenario": req.scenario,
                        "taxpayer_role": req.taxpayer_role,
                        "identity_key": id_key,
                    }
                )
                continue

        skipped_undetermined += 1

    if not dry_run:
        session.commit()

    return {
        "total": total,
        "updated": updated,
        "skipped_already_set": skipped_already_set,
        "skipped_undetermined": skipped_undetermined,
        "preview": results,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Backfill identity_keys on TaxRequirement table.")
    ap.add_argument("--country", default="", help="Country filter (e.g. TW)")
    ap.add_argument("--tax-key", default="", help="Tax key filter (e.g. tw_income)")
    ap.add_argument("--yes", action="store_true", help="Apply changes (default is dry-run preview)")
    args = ap.parse_args()

    init_db()
    session = get_session()
    try:
        dry_run = not args.yes
        stats = backfill_identity_keys(
            session,
            country=args.country or None,
            tax_key=args.tax_key or None,
            dry_run=dry_run,
        )
        mode = "PREVIEW" if dry_run else "EXECUTED"
        print(f"[{mode}] Total: {stats['total']}, Updated: {stats['updated']}, Already set: {stats['skipped_already_set']}, Undetermined: {stats['skipped_undetermined']}")
        for item in stats["preview"]:
            print(f"  #{item['id']} -> {item['identity_key']} ({item['scenario']} / {item['taxpayer_role']})")
        if dry_run and stats["updated"] > 0:
            print("\nRun with --yes to commit changes.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
