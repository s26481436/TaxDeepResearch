"""診斷增值稅關聯圖為什麼建不起來 — 只印可手抄的數字。

前一支腳本（diag_vat_rates_compact.py）已確認：
  - 資料庫裡有 75 條含「5%」的條文
  - 但母法條文掛上的補充規定 supp=0，prompt 裡只出現 1 次 5%
  - 沒有截斷（TRUNC=0）

也就是說料在庫裡，卻沒有 LegalRelation 把它們接到增值税法。
這支腳本查「為什麼接不上」。只讀資料庫，不寫入。

用法：python scripts/diag_vat_graph.py
"""

from sqlalchemy import func

from taxwatch.db import get_session
from taxwatch.graph.hierarchy import entity_key_for_title
from taxwatch.graph.resolver import normalize_entity_key
from taxwatch.models import (
    Document,
    LegalEntity,
    LegalRelation,
    ProvisionNode,
    RelationType,
    Snapshot,
)

s = get_session()
out = []

# --- G1. 關聯圖整體規模 --------------------------------------------------
n_ent = s.query(LegalEntity).count()
n_rel = s.query(LegalRelation).count()
out.append(f"G1 entities={n_ent} relations={n_rel}")
by_type = s.query(LegalRelation.relation_type, func.count()).group_by(
    LegalRelation.relation_type
).all()
out.append("G2 RELTYPES " + " ".join(f"{t.value}={c}" for t, c in by_type))

# 條號層級的邊（entity_key 含 #）佔多少 —— consolidated view 只認這種
art_level = (
    s.query(LegalRelation)
    .join(LegalEntity, LegalRelation.to_entity_id == LegalEntity.id)
    .filter(LegalEntity.entity_key.contains("#"))
    .count()
)
out.append(f"G3 to_article_level={art_level} to_doc_level={n_rel - art_level}")

# --- G4. 增值税法這個 entity 存不存在、鍵長什麼樣 -------------------------
VAT_TITLES = ["中华人民共和国增值税法", "增值税法"]
keys = [normalize_entity_key(t) for t in VAT_TITLES]
out.append("G4 normkeys " + " | ".join(keys))

vat_ent = None
for k in keys:
    e = s.query(LegalEntity).filter_by(entity_key=k).first()
    if e:
        vat_ent = e
        break
out.append(f"G5 vat_entity={'YES:' + vat_ent.entity_key if vat_ent else 'NO'}")

if vat_ent:
    doc_key = vat_ent.entity_key
    # 這份法的條號層 entity 有幾個
    n_art_ent = s.query(LegalEntity).filter(LegalEntity.entity_key.like(f"{doc_key}#%")).count()
    # 指向這份法（含條號層）的邊有幾條
    targets = s.query(LegalEntity.id).filter(
        (LegalEntity.entity_key == doc_key) | (LegalEntity.entity_key.like(f"{doc_key}#%"))
    )
    tids = [r[0] for r in targets.all()]
    n_in = s.query(LegalRelation).filter(LegalRelation.to_entity_id.in_(tids)).count()
    n_in_impl = (
        s.query(LegalRelation)
        .filter(
            LegalRelation.to_entity_id.in_(tids),
            LegalRelation.relation_type.in_([RelationType.AUTHORITY_OF, RelationType.INTERPRETS]),
        )
        .count()
    )
    out.append(f"G6 vat_article_entities={n_art_ent} inbound_all={n_in} inbound_implementing={n_in_impl}")

# --- G7. 那些含 5% 的條文，屬於哪些文件、有沒有連到增值税法 ---------------
rows = (
    s.query(Document.id, Document.title, Document.doc_type, ProvisionNode.node_key)
    .join(Snapshot, Snapshot.document_id == Document.id)
    .join(ProvisionNode, ProvisionNode.snapshot_id == Snapshot.id)
    .filter(ProvisionNode.text.contains("5%"))
    .all()
)
docs_with_5pct = {}
for did, title, dtype, nk in rows:
    docs_with_5pct.setdefault((did, title, dtype.value), []).append(nk)
out.append(f"G7 docs_containing_5pct={len(docs_with_5pct)}")

for i, ((did, title, dtype), nks) in enumerate(sorted(docs_with_5pct.items(), key=lambda x: -len(x[1]))[:6]):
    ekey = entity_key_for_title(title)
    ent = s.query(LegalEntity).filter_by(entity_key=ekey).first()
    # 這份文件（任一層）射出去的邊
    if ent:
        srcs = s.query(LegalEntity.id).filter(
            (LegalEntity.entity_key == ekey) | (LegalEntity.entity_key.like(f"{ekey}#%"))
        )
        sids = [r[0] for r in srcs.all()]
        n_out = s.query(LegalRelation).filter(LegalRelation.from_entity_id.in_(sids)).count()
    else:
        n_out = -1
    out.append(
        f"G8.{i} n={len(nks)} type={dtype} ent={'Y' if ent else 'N'} outbound={n_out} :: {title[:34]}"
    )

# --- G9. node_key 撞名程度 ----------------------------------------------
dup = (
    s.query(ProvisionNode.node_key, func.count(func.distinct(Snapshot.document_id)).label("d"))
    .join(Snapshot, ProvisionNode.snapshot_id == Snapshot.id)
    .group_by(ProvisionNode.node_key)
    .having(func.count(func.distinct(Snapshot.document_id)) > 1)
    .count()
)
out.append(f"G9 node_keys_used_by_multiple_docs={dup}")

# --- G10. 建圖有沒有跑過 --------------------------------------------------
from taxwatch.models import JobRun

jobs = (
    s.query(JobRun.job_type, func.count(), func.max(JobRun.finished_at))
    .group_by(JobRun.job_type)
    .all()
)
out.append("G10 JOBS " + " ".join(f"{t}={c}" for t, c, _ in jobs))

print("\n".join(out))
s.close()
