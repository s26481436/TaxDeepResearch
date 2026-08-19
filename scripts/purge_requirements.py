r"""刪除申報規範列（含欄位），刪除前一律先備份。

重新產生的矩陣若情境或身分措辭改變，舊列不會被 upsert 覆蓋而會成為孤兒。
此腳本用來清掉指定範圍後重新匯入。

預設為預覽模式，不會刪除任何東西。確認範圍無誤後才加 --yes。

用法：
    py .\scripts\purge_requirements.py --country TW                  # 預覽
    py .\scripts\purge_requirements.py --country TW --yes            # 執行
    py .\scripts\purge_requirements.py --country TW --tax-key tw_other --yes
    py .\scripts\purge_requirements.py --country TW --source import --yes

備份寫到 backups/requirements-<timestamp>.json，包含每一列與每一格的
完整內容，可用 --restore 還原。
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from taxwatch.db import get_session
from taxwatch.models import FieldSource, RequirementField, TaxRequirement


def _dump(rows: list[TaxRequirement]) -> list[dict]:
    return [
        {
            "country": r.country,
            "tax_key": r.tax_key,
            "scenario": r.scenario,
            "taxpayer_role": r.taxpayer_role,
            "status": r.status.value,
            "source_document_id": r.source_document_id,
            "model": r.model,
            "prompt_version": r.prompt_version,
            "notes": r.notes,
            "fields": [
                {
                    "field_key": f.field_key,
                    "value": f.value,
                    "citations": f.citations,
                    "confidence": f.confidence,
                    "source": f.source.value if f.source else None,
                    "needs_review": f.needs_review,
                    "review_reason": f.review_reason,
                }
                for f in r.fields
            ],
        }
        for r in rows
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--country")
    ap.add_argument("--tax-key")
    ap.add_argument("--source", choices=[s.value for s in FieldSource])
    ap.add_argument("--yes", action="store_true", help="實際執行刪除")
    ap.add_argument("--restore", help="從備份檔還原，忽略其他篩選條件")
    args = ap.parse_args()

    session = get_session()

    if args.restore:
        payload = json.loads(Path(args.restore).read_text(encoding="utf-8"))
        for row in payload:
            req = TaxRequirement(
                country=row["country"],
                tax_key=row["tax_key"],
                scenario=row["scenario"],
                taxpayer_role=row["taxpayer_role"],
                notes=row.get("notes", ""),
            )
            session.add(req)
            session.flush()
            for f in row["fields"]:
                session.add(
                    RequirementField(
                        requirement_id=req.id,
                        field_key=f["field_key"],
                        value=f["value"],
                        citations=f["citations"],
                        confidence=f["confidence"],
                        source=FieldSource(f["source"]) if f["source"] else FieldSource.IMPORT,
                        needs_review=f["needs_review"],
                        review_reason=f["review_reason"],
                    )
                )
        session.commit()
        print(f"已還原 {len(payload)} 列")
        return 0

    query = session.query(TaxRequirement)
    if args.country:
        query = query.filter(TaxRequirement.country == args.country.upper())
    if args.tax_key:
        query = query.filter(TaxRequirement.tax_key == args.tax_key)

    rows = query.all()
    if args.source:
        wanted = FieldSource(args.source)
        rows = [r for r in rows if any(f.source == wanted for f in r.fields)]

    if not rows:
        print("沒有符合條件的規範列。")
        return 0

    by_tax: dict[str, int] = {}
    for r in rows:
        by_tax[f"{r.country}/{r.tax_key}"] = by_tax.get(f"{r.country}/{r.tax_key}", 0) + 1

    print(f"符合條件：{len(rows)} 列、{sum(len(r.fields) for r in rows)} 格")
    for k, v in sorted(by_tax.items()):
        print(f"  {k}: {v} 列")

    if not args.yes:
        print("\n預覽模式，未刪除任何資料。確認無誤後加上 --yes 執行。")
        return 0

    backup_dir = Path("backups")
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = backup_dir / f"requirements-{stamp}.json"
    backup_path.write_text(
        json.dumps(_dump(rows), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n已備份至 {backup_path}")

    for r in rows:
        session.delete(r)  # fields cascade via all, delete-orphan
    session.commit()
    print(f"已刪除 {len(rows)} 列。還原：--restore {backup_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
