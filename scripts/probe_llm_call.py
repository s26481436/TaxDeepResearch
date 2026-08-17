r"""探針：重現 extract-requirements 的第一次 LLM 呼叫，只印關鍵資訊。

先確認 .env 的設定有沒有生效，再用真實的第一批 prompt 打一次 LLM，
把例外的類別與訊息開頭印出來。不寫資料庫。

用法：py .\scripts\probe_llm_call.py
"""

import traceback

from taxwatch.config import get_settings
from taxwatch.db import get_session
from taxwatch.requirements.extract import _render_batches
from taxwatch.requirements.prompts import (
    EXTRACTION_TEMPLATE,
    SYSTEM_PROMPT,
    format_field_definitions,
)
from taxwatch.requirements.schema import RequirementSetOut
from taxwatch.services.consolidated import get_consolidated
from taxwatch.services.documents import list_statutes_for_tax

out = []
st = get_settings()

# P1：設定有沒有生效（timeout 若仍是 120，代表 .env 沒被讀到）
out.append(
    f"P1 timeout={st.llm_timeout} max_tokens={st.llm_max_tokens} "
    f"model={st.llm_model} base={st.llm_base_url}"
)

s = get_session()
docs = list_statutes_for_tax(s, "CN", "cn_vat")
out.append(f"P2 docs={len(docs)}")
if not docs:
    out.append("P3 NO_DOCS — 資料庫沒有 cn_vat 的法規，問題不在 LLM")
    print("\n".join(out))
    raise SystemExit(0)

view = get_consolidated(s, docs[0].external_id)
batches = _render_batches(view)
out.append(f"P3 batches={len(batches)} first_batch_chars={len(batches[0][0]) if batches else 0}")

prompt = EXTRACTION_TEMPLATE.format(
    tax_name="增值稅",
    existing_scenarios_section="",
    field_definitions=format_field_definitions(),
    provisions=batches[0][0],
)
out.append(f"P4 prompt_chars={len(prompt)} system_chars={len(SYSTEM_PROMPT)}")

# P5：能力偵測（小請求，不受大 prompt 影響）
from taxwatch.analysis.client import get_llm_client

client = get_llm_client()
try:
    level = client.detect_capabilities()
    out.append(f"P5 capabilities={level.value}")
except Exception as exc:
    out.append(f"P5 DETECT_FAILED {type(exc).__name__}: {str(exc)[:120]}")
    print("\n".join(out))
    raise SystemExit(0)

# P6：真正的大請求
try:
    result = client.generate_structured(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=prompt,
        output_model=RequirementSetOut,
        max_retries=0,
    )
    out.append(f"P6 OK requirements={len(result.requirements)}")
except Exception as exc:
    out.append(f"P6 FAILED {type(exc).__name__}")
    out.append(f"P7 MSG {str(exc)[:200]}")
    tb = traceback.format_exc().strip().splitlines()
    out.append(f"P8 LAST {tb[-1][:200]}")

print("\n".join(out))
s.close()
