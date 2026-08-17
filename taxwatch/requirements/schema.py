"""Structured output contract for 申報規範 extraction.

Every cell must arrive with the provisions it came from. A rate with no
citation is a number the model produced, and there is no way to tell those
apart after the fact — so an uncited cell is stored at zero confidence and
surfaced as unverified rather than quietly mixed in with sourced guidance.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProvisionCitation(BaseModel):
    node_key: str = Field(
        default="",
        description="條文節點鍵，例如 增值税法#32。必須是輸入資料中實際出現過的節點鍵。",
    )
    title: str = Field(default="", description="法規名稱")
    quote: str = Field(
        default="",
        description="支持此欄位內容的條文原文片段（逐字引用，不要改寫）",
    )


class RequirementFieldOut(BaseModel):
    field_key: str = Field(description="欄位鍵，必須是指定清單中的其中之一")
    value: str = Field(description="欄位內容（繁體中文）")
    citations: list[ProvisionCitation] = Field(
        default_factory=list,
        description="此欄位所依據的條文。無法從條文推得時留空並將 confidence 設為 0。",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="0-1。條文明文寫出者接近 1；需要推論者降低；無條文依據為 0。",
    )


class RequirementOut(BaseModel):
    scenario: str = Field(description="子項目／課稅情境，例如「一般貨物及勞務銷售」")
    taxpayer_role: str = Field(
        default="",
        description="納稅／扣繳／代徵角色，例如「一般納稅人 - 一般計稅」",
    )
    fields: list[RequirementFieldOut] = Field(default_factory=list)


class RequirementSetOut(BaseModel):
    """All scenarios the model could identify for one tax type."""

    requirements: list[RequirementOut] = Field(
        ...,
        description="從條文中識別出的所有課稅情境",
    )
    unresolved: list[str] = Field(
        default_factory=list,
        description="條文不足以判斷、需要人工補充的項目說明",
    )
