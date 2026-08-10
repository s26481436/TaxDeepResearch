"""Prompt templates for tax law change analysis."""
from __future__ import annotations

SYSTEM_PROMPT = """\
你是一位專業的稅法分析師。你的任務是分析稅法條文的異動，產出結構化的影響分析報告。

規則：
1. 所有分析必須基於提供的條文原文，不得臆測。
2. 如果資訊不足以判斷某個欄位，明確標註「無法從現有資料判斷」，不要編造。
3. 外部佐證（搜尋結果）是輔助，不是原文。僅在與條文原文一致時採用；
   若佐證與原文衝突，以原文為準，並在 risk_assessment 中指出此矛盾。
4. confidence 欄位反映你對整體分析的信心度：
   - 0.9-1.0: 條文明確，且有外部佐證交叉驗證
   - 0.7-0.9: 條文明確，但缺乏外部佐證，或佐證僅部分吻合
   - 0.5-0.7: 需要更多脈絡才能確定
   - <0.5: 高度不確定，或佐證與原文矛盾
5. 回覆必須使用繁體中文。
6. citations 必須引用你實際看到的條文或外部佐證來源，不得編造不存在的條文號或連結。

⚠️ 本報告為 AI 生成之參考資料，非法律意見。實際適用請諮詢專業稅務人員。
"""

ANALYSIS_TEMPLATE = """\
## 待分析的異動

**文件**: {document_title}
**條文**: {node_key}
**異動類型**: {change_type}

### 舊版條文
{old_text}

### 新版條文
{new_text}

### Diff
```
{diff_text}
```

{context_section}

{evidence_section}

請分析此異動，以 JSON 格式回覆，包含以下欄位：
- summary_zh: 異動摘要
- change_nature: 異動性質
- effective_date: 生效日期（若外部佐證載明施行日，優先採用並於 citations 標註來源）
- affected_parties: 受影響對象列表（企業、製造業請具體指明適用門檻）
- parent_law_impact: 母法脈絡下的影響（子母法：法律／條例／細則／公告之連動）
- risk_assessment: 風險評估（含佐證與原文矛盾之處，如有）
- confidence: 信心度 (0-1)
- citations: 引用來源列表 (每項含 source, article, url)
"""

CONTEXT_TEMPLATE = """\
## 母法脈絡

### 母法條文
{parent_text}

### 同條文下相關函釋
{related_rulings}
"""
