"""Pydantic models for structured LLM output."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CitationRef(BaseModel):
    source: str = Field(description="引用來源的法規或文件名稱")
    article: str = Field(default="", description="條文編號")
    url: str = Field(default="", description="原文連結")


class ChangeAnalysis(BaseModel):
    summary_zh: str = Field(description="異動摘要（繁體中文）")
    change_nature: str = Field(
        description=(
            "異動性質：新增條文 / 實質修改 / 稅率調整 / 門檻調整 / 程序變更 / 文字修正 / 刪除"
        )
    )
    effective_date: str = Field(default="", description="生效日期（如可判斷）")
    affected_parties: list[str] = Field(
        default_factory=list,
        description="受影響對象，如：個人納稅義務人、營利事業、扣繳義務人",
    )
    parent_law_impact: str = Field(
        default="",
        description="對母法的影響分析：此異動在母法脈絡下的意義",
    )
    risk_assessment: str = Field(
        default="",
        description="風險評估：對既有申報、稅務規劃的潛在影響",
    )
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="分析信心度 (0-1)",
    )
    citations: list[CitationRef] = Field(
        default_factory=list,
        description="引用的法規來源",
    )
