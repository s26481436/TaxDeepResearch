"""Prompt templates for tax law change analysis."""
from __future__ import annotations

SYSTEM_PROMPT = """\
你是一位專業的稅法分析師。你的任務是分析稅法條文的異動，產出結構化的影響分析報告。

規則：
1. 所有分析必須基於提供的條文原文，不得臆測。
2. 如果資訊不足以判斷某個欄位，明確標註「無法從現有資料判斷」，不要編造。
3. confidence 欄位反映你對整體分析的信心度：
   - 1.0: 條文明確，分析確定
   - 0.7-0.9: 大部分確定，部分需要進一步確認
   - 0.5-0.7: 需要更多脈絡才能確定
   - <0.5: 高度不確定
4. 回覆必須使用繁體中文。
5. citations 必須引用你實際看到的條文，不得編造不存在的條文號。

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

請分析此異動，以 JSON 格式回覆，包含以下欄位：
- summary_zh: 異動摘要
- change_nature: 異動性質
- effective_date: 生效日期
- affected_parties: 受影響對象列表
- parent_law_impact: 母法脈絡下的影響
- risk_assessment: 風險評估
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
