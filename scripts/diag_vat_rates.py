"""診斷：增值稅簡易計稅為什麼只抽到 3% 一種稅率。

用法（在專案根目錄）：
    python /Users/peiyujhang/.claude/jobs/6a472564/tmp/diag_vat_rates.py

只讀資料庫，不寫入任何東西。
"""

from taxwatch.db import get_session
from taxwatch.models import RequirementField, TaxRequirement
from taxwatch.requirements.extract import _MAX_PROVISION_CHARS, _render_provisions
from taxwatch.services.consolidated import get_consolidated
from taxwatch.services.documents import list_statutes_for_tax

RATE_TOKENS = ["3%", "5%", "1.5%", "0.5%", "2%", "百分之三", "百分之五", "征收率", "徵收率"]

session = get_session()

print("=" * 70)
print("1. 已存入的 cn_vat 申報規範列")
print("=" * 70)
rows = session.query(TaxRequirement).filter_by(country="CN", tax_key="cn_vat").all()
print(f"共 {len(rows)} 列\n")
for r in rows:
    rate = next((f for f in r.fields if f.field_key == "rate"), None)
    print(f"  [{r.id}] {r.scenario} / {r.taxpayer_role}")
    if rate:
        print(f"        稅率: {rate.value[:120]}")
        print(f"        引用: {[c.get('node_key') for c in rate.citations]}")
    else:
        print("        稅率: (無此欄位)")

print()
print("=" * 70)
print("2. 這個稅種的抽取入口文件（只有 STATUTE / REGULATION 會被當入口）")
print("=" * 70)
docs = list_statutes_for_tax(session, "CN", "cn_vat")
for d in docs:
    print(f"  {d.doc_type.value:12} {d.external_id:30} {d.title}")

print()
print("=" * 70)
print("3. 每份入口文件的 consolidated view 實際送進 prompt 的量")
print("=" * 70)
for d in docs:
    try:
        view = get_consolidated(session, d.external_id)
    except Exception as exc:
        print(f"  {d.title}: 取不到 consolidated view — {type(exc).__name__}: {exc}")
        continue

    n_articles = len(view["articles"])
    n_supp = sum(len(a["supplements"]) for a in view["articles"])
    n_unanchored = len(view.get("unanchored_supplements", []))

    block, allowed = _render_provisions(view)

    # 重算「完整渲染會有多長」，跟預算比對，判斷是否被截斷
    full_len = 0
    for a in view["articles"]:
        full_len += len(f"\n### [{a['node_key']}] {a['heading']}\n{a['text']}")
        for s in a["supplements"]:
            full_len += len(
                f"\n  補充規定 [{s['node_key']}] 《{s['document_title']}》{s['heading']}\n  {s['text']}"
            )
    for s in view.get("unanchored_supplements", []):
        full_len += len(f"\n### [{s['node_key']}] 《{s['document_title']}》{s['heading']}\n{s['text']}")

    truncated = full_len > _MAX_PROVISION_CHARS
    print(f"\n  《{view['title']}》")
    print(f"    母法條文 {n_articles} 條，掛上的補充規定 {n_supp} 條，未錨定補充 {n_unanchored} 條")
    print(f"    子法: {[c['title'] for c in view.get('child_documents', [])]}")
    print(f"    完整長度 {full_len:,} 字元 / 上限 {_MAX_PROVISION_CHARS:,}")
    print(f"    實際送出 {len(block):,} 字元，allowed_nodes {len(allowed)} 個")
    if truncated:
        print(f"    *** 被截斷，丟失約 {full_len - len(block):,} 字元 —— 而且沒有任何警告 ***")
        print(f"    *** allowed_nodes 仍含被截斷的節點（可引用但模型看不到）***")

    # prompt 裡到底有沒有出現 3% 以外的徵收率
    print("    prompt 內出現的稅率字樣:")
    for token in RATE_TOKENS:
        hits = block.count(token)
        if hits:
            print(f"      {token:8} × {hits}")

print()
print("=" * 70)
print("4. 資料庫裡有沒有講到其他徵收率的文件（不論 doc_type）")
print("=" * 70)
from taxwatch.models import Document, ProvisionNode, Snapshot

for token in ["百分之五", "5%", "1.5%", "0.5%", "减按", "征收率"]:
    q = (
        session.query(Document.title, ProvisionNode.node_key, ProvisionNode.text)
        .join(Snapshot, Snapshot.document_id == Document.id)
        .join(ProvisionNode, ProvisionNode.snapshot_id == Snapshot.id)
        .filter(ProvisionNode.text.contains(token))
        .limit(5)
        .all()
    )
    print(f"\n  「{token}」— {len(q)} 筆（上限 5）")
    for title, node_key, text in q:
        print(f"    {node_key:20} 《{title}》")
        print(f"      {text[:100]}")

session.close()
