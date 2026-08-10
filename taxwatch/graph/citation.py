"""Citation extraction from legal text using regex patterns.

Taiwan legal citations follow highly regular patterns:
- 所得稅法第14條第1項第5類
- 台財稅字第10904512340號
- 釋字第745號
- 憲判字第5號

Regex covers the majority of cases; LLM fallback handles free-form references.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Citation:
    raw_text: str
    entity_key: str
    relation_type: str  # interprets, authority_of, amends, supersedes, cites
    confidence: float
    extracted_by: str  # regex or llm


_LAW_SUFFIX = r"(?:法|條例|準則|辦法|規則|細則)"
_LAW_NAME = rf"(?:[一-鿿]{{2,20}}{_LAW_SUFFIX})"
_RULING_NUM = r"(?:台|臺)財稅(?:發|字)?第\s*[\d]+\s*號"

_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # 依據/授權 (must come before generic law article to win priority)
    (
        re.compile(
            r"(?:依|依據|按|按照)\s*"
            + _LAW_NAME
            + r"第\s*(\d+(?:-\d+)?(?:之\d+)?)\s*條"
        ),
        "authority_of",
        "regex",
    ),
    # 修正/修改
    (
        re.compile(
            r"(?:修正|修改|增訂|刪除)\s*"
            + _LAW_NAME
            + r"第\s*(\d+(?:-\d+)?(?:之\d+)?)\s*條"
        ),
        "amends",
        "regex",
    ),
    # 停止適用/廢止 — pattern A: 停止適用 + 號碼
    (
        re.compile(
            r"(?:停止適用|廢止|不再適用)\s*"
            r"(" + _RULING_NUM + r")"
        ),
        "supersedes",
        "regex",
    ),
    # 停止適用/廢止 — pattern B: 號碼 + ...停止適用 (common in TW rulings)
    (
        re.compile(
            r"(" + _RULING_NUM + r")"
            r"[^。；\n]{0,20}(?:業經|已|應)?\s*(?:停止適用|廢止|不再適用)"
        ),
        "supersedes",
        "regex",
    ),
    # 法律條文引用: 所得稅法第14條、稅捐稽徵法第48條之1
    (
        re.compile(
            r"(" + _LAW_NAME + r")"
            r"第\s*(\d+(?:-\d+)?(?:之\d+)?)\s*條"
            r"(?:第\s*(\d+)\s*項)?"
            r"(?:第\s*(\d+)\s*(?:款|類|目))?"
        ),
        "interprets",
        "regex",
    ),
    # 財政部函釋
    (
        re.compile(r"(" + _RULING_NUM + r")"),
        "interprets",
        "regex",
    ),
    # 釋字
    (
        re.compile(r"(釋字第\s*(\d+)\s*號)"),
        "interprets",
        "regex",
    ),
    # 憲判字
    (
        re.compile(r"(憲判字第\s*(\d+)\s*號)"),
        "interprets",
        "regex",
    ),
]


def extract_citations(text: str) -> list[Citation]:
    citations: list[Citation] = []
    seen: set[str] = set()

    for pattern, relation_type, method in _PATTERNS:
        for match in pattern.finditer(text):
            raw = match.group(0).strip()
            entity_key = _match_to_entity_key(match, relation_type)
            dedup_key = f"{entity_key}:{relation_type}"
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            citations.append(Citation(
                raw_text=raw,
                entity_key=entity_key,
                relation_type=relation_type,
                confidence=1.0,
                extracted_by=method,
            ))

    return citations


def _match_to_entity_key(match: re.Match, relation_type: str) -> str:
    """Convert regex match to a stable entity_key."""
    groups = match.groups()
    full = match.group(0)

    # 法律條文: groups = (law_name, article_no, item?, subitem?)
    if len(groups) >= 2 and re.search(r"第\s*\d+.*條", full):
        law_name = _clean_law_name(groups[0]) if groups[0] else ""
        article_no = re.sub(r"\s+", "", groups[1])
        if law_name:
            key = f"{law_name}#{article_no}"
            if len(groups) > 2 and groups[2]:
                key += f"#{groups[2]}"
            return key

    # 函釋: 台財稅字第XXXXX號
    ruling_match = re.search(r"((?:台|臺)財稅(?:發|字)?第\s*[\d]+\s*號)", full)
    if ruling_match:
        return re.sub(r"\s+", "", ruling_match.group(1)).replace("臺", "台")

    # 釋字/憲判字
    interp_match = re.search(r"((?:釋字|憲判字)第\s*\d+\s*號)", full)
    if interp_match:
        return re.sub(r"\s+", "", interp_match.group(1))

    return re.sub(r"\s+", "", full)


def _clean_law_name(name: str) -> str:
    """Strip leading verb prefixes that regex may capture before the actual law name."""
    prefixes = ["依據", "依", "按照", "按", "修正", "修改", "增訂", "刪除", "查"]
    for p in sorted(prefixes, key=len, reverse=True):
        if name.startswith(p):
            name = name[len(p):]
            break
    return name.strip()
