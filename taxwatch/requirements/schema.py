"""Structured output contract for 申報規範 extraction.

Every cell must arrive with the provisions it came from. A rate with no
citation is a number the model produced, and there is no way to tell those
apart after the fact — so an uncited cell is stored at zero confidence and
surfaced as unverified rather than quietly mixed in with sourced guidance.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ProvisionCitation(BaseModel):
    """What the model must say to point at a provision.

    Deliberately minimal. Every token here is paid once per cell per row, and
    the matrix has eleven cells — a citation that echoes back text the caller
    already holds is the dominant cost of an extraction. The law's name is
    `node_key` up to the `#`, so asking for it separately buys nothing.
    """

    node_key: str = Field(
        default="",
        description="條文節點鍵，例如 增值税法#32。必須是輸入資料中實際出現過的節點鍵。",
    )
    quote: str = Field(
        default="",
        description=(
            "條文原文的**開頭 20 字以內**，用於定位是條文的哪一段。"
            "不要抄錄整條，系統已持有全文。"
        ),
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
    scenario: str = Field(description="子項目／課稅情境（人類可讀描述），例如「一般貨物及勞務銷售」")
    taxpayer_role: str = Field(
        default="",
        description="納稅／扣繳／代徵角色（人類可讀描述），例如「一般納稅人 - 一般計稅」",
    )
    taxpayer_class: str = Field(
        default="",
        description="受控納稅主體類別（例如 resident_individual），若有提供詞彙表請務必選擇其一",
    )
    tax_scheme: str = Field(
        default="",
        description="受控計稅／申報方式（例如 annual_filing），若有提供詞彙表請務必選擇其一",
    )
    subject_matter: str = Field(
        default="",
        description="受控課稅標的（例如 general_income），若有提供詞彙表請務必選擇其一",
    )
    scenario_key: str = Field(
        default="",
        description="受控情境細分鍵（例如 standard），若有提供詞彙表請務必選擇其一",
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
