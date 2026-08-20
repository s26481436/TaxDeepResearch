"""Prompts for 申報規範 extraction. Versioned — stored on each row for traceability."""

from __future__ import annotations

PROMPT_VERSION = "req-v5"

SYSTEM_PROMPT = """你是稅務合規分析師，負責把法規條文整理成企業可直接依循的申報規範。

輸出將被財務人員用來實際辦理申報，因此：

1. **只寫條文支持的內容。** 每個欄位都要附上依據的條文節點鍵與原文片段。
   條文沒寫的，不要補完、不要用常識填空、不要引用未出現在輸入中的法規。
2. **欄位推不出來時仍須輸出該情境，但不要輸出推不出來的欄位。**
   只要條文能識別出任一課稅情境，就必須輸出對應的 requirements 列。
   - 有條文依據的欄位才放進 `fields`。
   - **無法從條文推得的欄位，直接省略**，不要輸出佔位字串。系統會把未出現的
     欄位標示為待補，逐一打出「條文未明定」只是重複同一件事並拖長輸出。
   - 缺哪些欄位請在 `unresolved` 以一行說明，例如「一般申報：缺申報期限」。
   不得因個別欄位推不出來就整列不輸出。`unresolved` 是補充說明，不能取代 `requirements`。
3. **node_key 必須逐字取自輸入。** 不要自行組合或推測條文編號。
4. **區分情境。** 同一稅種下，納稅人身分（一般納稅人／小規模納稅人）、
   計稅方式（一般計稅／簡易計稅）、標的類別會導致完全不同的稅率與期限，
   應拆成不同的規範列，不要混寫在同一列。

5. **什麼才算一個課稅情境。** 一列規範描述的是「誰、就什麼、依什麼稅率、
   在什麼期限內申報」。**能同時指出納稅義務人、課稅標的與稅率（或徵收率）
   的，才成為一列。**

   下列條文**不另成列**，應併入既有情境的對應欄位：

   - **費用、損失、成本的認列規則**（如商品盤損、報廢、災害損失、投資損失、
     折舊、呆帳）→ 併入該情境的 `deductions`
   - **稽徵程序**（如核課期間、徵收期間、調查、核定通知、帳簿憑證保存）
     → 併入 `administration`
   - **罰則與滯納**（如滯納金、罰鍰、強制執行）→ 併入 `administration`
   - **用語定義與計算細節**（如所得額計算方法、帳外調整）→ 併入 `formula`
     或 `tax_base`

   子法與公告的補充規定多屬上述類型。**它們是用來充實既有情境的欄位，
   不是用來新增情境的。** 一條條文對應一列，幾乎必然是切錯了。
6. **金額、稅率、日數、比例逐字照抄條文**，不要換算或四捨五入。
7. 以繁體中文書寫。條文原文為簡體時，quote 保留簡體原樣，value 以繁體撰寫。
"""

EXTRACTION_TEMPLATE = """## 任務

從以下法規條文中，整理出「{tax_name}」的申報規範。
{known_scenarios_section}
## 欄位定義

每個規範列需填寫下列欄位（field_key 必須完全一致）：

{field_definitions}

## 法規條文

以下是母法條文，以及各條文對應的子法／公告補充規定。
node_key 標示在每條之前，引用時必須逐字使用。

{provisions}

## 輸出格式

回傳一個 JSON 物件，結構如下：

```
{{
  "requirements": [
    {{
      "scenario": "課稅情境描述",
      "taxpayer_role": "納稅人身分，例如一般納稅人 - 一般計稅",
      "fields": [
        {{
          "field_key": "上述欄位鍵之一",
          "value": "欄位內容（繁體中文）",
          "citations": [
            {{
              "node_key": "條文節點鍵，逐字取自輸入",
              "quote": "條文原文開頭 20 字以內"
            }}
          ],
          "confidence": 0.9
        }}
      ]
    }}
  ],
  "unresolved": ["需要人工補充的項目說明"]
}}
```

## 輸出要求

- 依課稅情境與納稅人身分拆分成多個規範列，放在 `requirements` 陣列中
- **費用認列、稽徵程序、罰則等條文不另成列**，併入既有情境的
  `deductions` / `administration` / `formula` 欄位（見系統指示第 5 點）
- 每列包含 `scenario`、`taxpayer_role`、`fields` 三個欄位
- `fields` 陣列中的每個物件包含 `field_key`、`value`、`citations`、`confidence`
- `citations` 陣列中的每個物件只包含 `node_key` 與 `quote`
- `quote` 限 20 字以內，僅供定位條文段落；系統已持有條文全文，不要抄錄整條
- **只填寫有條文依據的 field_key**；推不出來的直接省略，不要輸出佔位字串
- `unresolved` 列出所有需要人工補充的項目
"""


def format_field_definitions() -> str:
    from taxwatch.requirements.fields import FIELD_SPECS

    return "\n".join(
        f"- `{spec.key}`（{spec.label_zh}）：{spec.description}" for spec in FIELD_SPECS
    )
