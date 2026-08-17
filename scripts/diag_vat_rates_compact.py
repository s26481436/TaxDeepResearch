"""診斷增值稅簡易計稅稅率缺漏 — 精簡版，只印可手抄的數字。

用法：python scripts/diag_vat_rates_compact.py
只讀資料庫，不寫入。
"""

from taxwatch.db import get_session
from taxwatch.models import Document, ProvisionNode, Snapshot, TaxRequirement
from taxwatch.requirements.extract import _MAX_PROVISION_CHARS, _render_provisions
from taxwatch.services.consolidated import get_consolidated
from taxwatch.services.documents import list_statutes_for_tax

s = get_session()
out = []

# --- 1. 現有規範列與稅率 -------------------------------------------------
rows = (
    s.query(TaxRequirement)
    .filter(TaxRequirement.tax_key.in_(["cn_vat", "vat"]), TaxRequirement.country == "CN")
    .all()
)
rates = []
for r in rows:
    f = next((x for x in r.fields if x.field_key == "rate"), None)
    v = (f.value if f else "").replace("\n", " ")[:24] or "-"
    rates.append(v)
out.append(f"L1 ROWS={len(rows)}")
for i, (r, v) in enumerate(zip(rows, rates)):
    out.append(f"L1.{i} {r.taxpayer_role[:16] or '-'} :: {v}")

# --- 2/3. 入口文件與 consolidated view ----------------------------------
docs = list_statutes_for_tax(s, "CN", "cn_vat")
out.append(f"L2 DOCS={len(docs)}")
for i, d in enumerate(docs):
    try:
        view = get_consolidated(s, d.external_id)
    except Exception as e:
        out.append(f"L3.{i} ERR={type(e).__name__}")
        continue
    arts = view["articles"]
    supp = sum(len(a["supplements"]) for a in arts)
    unan = len(view.get("unanchored_supplements", []))
    kids = len(view.get("child_documents", []))
    block, allowed = _render_provisions(view)

    full = 0
    for a in arts:
        full += len(f"\n### [{a['node_key']}] {a['heading']}\n{a['text']}")
        for x in a["supplements"]:
            full += len(
                f"\n  補充規定 [{x['node_key']}] 《{x['document_title']}》{x['heading']}\n  {x['text']}"
            )
    for x in view.get("unanchored_supplements", []):
        full += len(f"\n### [{x['node_key']}] 《{x['document_title']}》{x['heading']}\n{x['text']}")

    trunc = 1 if full > _MAX_PROVISION_CHARS else 0
    out.append(
        f"L3.{i} art={len(arts)} supp={supp} unan={unan} kids={kids} "
        f"full={full} sent={len(block)} nodes={len(allowed)} TRUNC={trunc}"
    )
    # prompt 裡出現的徵收率字樣
    hits = {t: block.count(t) for t in ["3%", "5%", "1.5%", "0.5%", "减按", "征收率"]}
    out.append("L4." + str(i) + " PROMPT " + " ".join(f"{k}={v}" for k, v in hits.items()))

# --- 4. 全庫有沒有這些稅率 ----------------------------------------------
db = {}
for t in ["百分之五", "5%", "1.5%", "0.5%", "减按", "征收率"]:
    db[t] = (
        s.query(ProvisionNode)
        .join(Snapshot, ProvisionNode.snapshot_id == Snapshot.id)
        .filter(ProvisionNode.text.contains(t))
        .count()
    )
out.append("L5 DB " + " ".join(f"{k}={v}" for k, v in db.items()))

# 全庫文件數 by doc_type
from sqlalchemy import func

types = s.query(Document.doc_type, func.count()).group_by(Document.doc_type).all()
out.append("L6 DOCTYPES " + " ".join(f"{t.value}={c}" for t, c in types))

print("\n".join(out))
s.close()
