r"""看剛匯入的申報規範矩陣長什麼形狀 — 階段 2 標籤詞彙的設計依據。

只讀資料庫。用法：py .\scripts\inspect_imported_matrix.py
可加參數限定轄區：py .\scripts\inspect_imported_matrix.py TW
"""

import sys
from collections import Counter, defaultdict

from taxwatch.db import get_session
from taxwatch.models import FieldSource, RequirementField, TaxRequirement

country = sys.argv[1].upper() if len(sys.argv) > 1 else None

s = get_session()
q = s.query(TaxRequirement)
if country:
    q = q.filter(TaxRequirement.country == country)
rows = q.all()

out = []
out.append(f"M1 ROWS={len(rows)} country={country or 'ALL'}")

# 每個稅種幾列
by_tax = Counter(f"{r.country}/{r.tax_key}" for r in rows)
out.append("M2 BY_TAX " + " ".join(f"{k}={v}" for k, v in by_tax.most_common()))

# 來源分布（IMPORT = 匯入，LLM = 抽取）
src = Counter(
    f.source.value for r in rows for f in r.fields if f.source
)
out.append("M3 FIELD_SOURCE " + " ".join(f"{k}={v}" for k, v in src.most_common()))

# 有多少格帶著可用的條文引用
cited = sum(1 for r in rows for f in r.fields if f.citations)
total = sum(len(r.fields) for r in rows)
out.append(f"M4 CITED_FIELDS={cited}/{total}")

# 每個稅種底下的 taxpayer_role 分布 — 這是維度 1 的設計依據
roles = defaultdict(Counter)
for r in rows:
    roles[f"{r.country}/{r.tax_key}"][r.taxpayer_role.strip() or "(空)"] += 1
for i, (tax, counter) in enumerate(sorted(roles.items())):
    out.append(f"M5.{i} {tax} roles={len(counter)}")
    for role, n in counter.most_common():
        out.append(f"      {n:>3}x {role[:44]}")

# scenario 措辭樣本 — 維度 2 與 scenario_key 的設計依據
out.append("M6 SCENARIO_SAMPLES (每個稅種最多 12 個)")
scen = defaultdict(list)
for r in rows:
    scen[f"{r.country}/{r.tax_key}"].append(r.scenario.strip())
for tax, items in sorted(scen.items()):
    out.append(f"  [{tax}] {len(items)} 列 / {len(set(items))} 種說法")
    for sc in sorted(set(items))[:12]:
        out.append(f"      {sc[:52]}")

print("\n".join(out))
s.close()
