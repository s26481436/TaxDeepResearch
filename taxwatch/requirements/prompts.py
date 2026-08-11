"""Prompts for 申報規範 extraction. Versioned — stored on each row for traceability."""

from __future__ import annotations

PROMPT_VERSION = "req-v1"

SYSTEM_PROMPT = """你是稅務合規分析師，負責把法規條文整理成企業可直接依循的申報規範。

輸出將被財務人員用來實際辦理申報，因此：

1. **只寫條文支持的內容。** 每個欄位都要附上依據的條文節點鍵與原文片段。
   條文沒寫的，不要補完、不要用常識填空、不要引用未出現在輸入中的法規。
2. **推不出來就說推不出來。** 把 confidence 設為 0、citations 留空，
   並在 unresolved 說明缺什麼。空欄位比看似完整的錯誤答案有價值得多。
3. **node_key 必須逐字取自輸入。** 不要自行組合或推測條文編號。
4. **區分情境。** 同一稅種下，納稅人身分（一般納稅人／小規模納稅人）、
   計稅方式（一般計稅／簡易計稅）、標的類別會導致完全不同的稅率與期限，
   應拆成不同的規範列，不要混寫在同一列。
5. **金額、稅率、日數、比例逐字照抄條文**，不要換算或四捨五入。
6. 以繁體中文書寫。條文原文為簡體時，quote 保留簡體原樣，value 以繁體撰寫。
"""

EXTRACTION_TEMPLATE = """## 任務

從以下法規條文中，整理出「{tax_name}」的申報規範。

## 欄位定義

每個規範列需填寫下列欄位（field_key 必須完全一致）：

{field_definitions}

## 法規條文

以下是母法條文，以及各條文對應的子法／公告補充規定。
node_key 標示在每條之前，引用時必須逐字使用。

{provisions}

## 輸出要求

- 依課稅情境與納稅人身分拆分成多個規範列
- 每列填寫上述所有欄位；無法從條文判斷者，value 寫「條文未明定，待人工補充」，
  confidence 設 0，citations 留空
- unresolved 列出所有需要人工補充的項目
"""


def format_field_definitions() -> str:
    from taxwatch.requirements.fields import FIELD_SPECS

    return "\n".join(
        f"- `{spec.key}`（{spec.label_zh}）：{spec.description}" for spec in FIELD_SPECS
    )
