"""驗收：稅率光譜是否保住、新舊抽取是否分得開。

舊列（PROMPT_VERSION=req-v2）與新列（req-v3）混在同一張表裡，
直接看總列數會誤導，所以依 prompt_version 分開統計。

只讀資料庫，不寫入。用法：py .\scripts\verify_rate_spectrum.py
"""

import re

from taxwatch.db import get_session
from taxwatch.models import TaxRequirement

# 修正前只有 3%，這幾個是本次要保住的成果
WANTED = {
    "5%": r"5\s*%",
    "3->2": r"3\s*%.*?2\s*%",
    "3->1.5": r"3\s*%.*?1\.5\s*%",
    "3->1": r"3\s*%.*?1\s*%(?!\d)",
    "3%": r"(?<!\.)\b3\s*%|百分之三",
    "13%": r"13\s*%|百分之十三",
}

s = get_session()
out = []

rows = s.query(TaxRequirement).filter_by(country="CN", tax_key="cn_vat").all()

by_ver = {}
for r in rows:
    by_ver.setdefault(r.prompt_version or "(空)", []).append(r)

out.append("V1 TOTAL=%d " % len(rows) + " ".join(f"{v}={len(g)}" for v, g in sorted(by_ver.items())))

for ver, group in sorted(by_ver.items()):
    rates = []
    for r in group:
        f = next((x for x in r.fields if x.field_key == "rate"), None)
        if f and f.value:
            rates.append(f.value)
    blob = " || ".join(rates)
    hits = {k: ("Y" if re.search(p, blob) else "N") for k, p in WANTED.items()}
    out.append(f"V2 {ver} rows={len(group)} rated={len(rates)} " + " ".join(f"{k}={v}" for k, v in hits.items()))

# 情境碎片化：同 taxpayer_role 出現幾種 scenario
frag = {}
for r in rows:
    frag.setdefault((r.prompt_version or "?", r.taxpayer_role.strip()), set()).add(r.scenario.strip())
worst = sorted(frag.items(), key=lambda x: -len(x[1]))[:3]
for i, ((ver, role), scs) in enumerate(worst):
    out.append(f"V3.{i} {ver} role={role[:18]} scenarios={len(scs)}")

print("\n".join(out))
s.close()
