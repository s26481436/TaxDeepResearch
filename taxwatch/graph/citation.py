"""Citation extraction from legal text using regex patterns.

Taiwan legal citations follow highly regular patterns:
- 所得稅法第14條第1項第5類
- 台財稅字第10904512340號
- 釋字第745號
- 憲判字第5號

China legal citations:
- 企业所得税法第X条 / 增值税暂行条例第X条
- 财税〔2026〕X号 / 国家税务总局公告2026年第X号
- 国税发〔2024〕X号

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


# --- TW patterns ---
_LAW_SUFFIX = r"(?:法|條例|準則|辦法|規則|細則)"
_LAW_NAME = rf"(?:[一-鿿]{{2,20}}{_LAW_SUFFIX})"
_RULING_NUM = r"(?:台|臺)財稅(?:發|字)?第\s*[\d]+\s*號"

# --- CN patterns ---
_CN_LAW_SUFFIX = r"(?:法|条例|细则|办法|规则|暂行条例|暂行办法|管理办法)"
_CN_LAW_NAME = rf"(?:[一-鿿]{{2,20}}{_CN_LAW_SUFFIX})"
_CN_WENHAO_CAISHUI = r"财税[〔\[]\d{4}[〕\]]\d+号"
_CN_WENHAO_GONGGAO = r"(?:国家税务总局|财政部\s*税务总局)公告\d{4}年第\d+号"
_CN_WENHAO_GUOSHUI = r"国税[发函][〔\[]\d{4}[〕\]]\d+号"
_CN_WENHAO_SHUIZONG = r"税总[发函][〔\[]\d{4}[〕\]]\d+号"
_CN_WENHAO = (
    rf"(?:{_CN_WENHAO_GONGGAO}|{_CN_WENHAO_CAISHUI}"
    rf"|{_CN_WENHAO_GUOSHUI}|{_CN_WENHAO_SHUIZONG})"
)

_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    # --- TW: 依據/授權 (must come before generic law article to win priority) ---
    (
        re.compile(r"(?:依|依據|按|按照)\s*" + _LAW_NAME + r"第\s*(\d+(?:-\d+)?(?:之\d+)?)\s*條"),
        "authority_of",
        "regex",
    ),
    # TW: 修正/修改
    (
        re.compile(
            r"(?:修正|修改|增訂|刪除)\s*" + _LAW_NAME + r"第\s*(\d+(?:-\d+)?(?:之\d+)?)\s*條"
        ),
        "amends",
        "regex",
    ),
    # TW: 停止適用/廢止 — pattern A
    (
        re.compile(
            r"(?:停止適用|廢止|不再適用)\s*"
            r"(" + _RULING_NUM + r")"
        ),
        "supersedes",
        "regex",
    ),
    # TW: 停止適用/廢止 — pattern B
    (
        re.compile(
            r"(" + _RULING_NUM + r")"
            r"[^。；\n]{0,20}(?:業經|已|應)?\s*(?:停止適用|廢止|不再適用)"
        ),
        "supersedes",
        "regex",
    ),
    # TW: 法律條文引用
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
    # TW: 財政部函釋
    (
        re.compile(r"(" + _RULING_NUM + r")"),
        "interprets",
        "regex",
    ),
    # TW: 釋字
    (
        re.compile(r"(釋字第\s*(\d+)\s*號)"),
        "interprets",
        "regex",
    ),
    # TW: 憲判字
    (
        re.compile(r"(憲判字第\s*(\d+)\s*號)"),
        "interprets",
        "regex",
    ),
    # --- CN: 依据/依照 (authority) ---
    (
        re.compile(
            r"(?:依据|依照|按照|根据)\s*"
            r"(?:《)?" + _CN_LAW_NAME + r"(?:》)?"
            r"第(\d+)条"
        ),
        "authority_of",
        "regex",
    ),
    # CN: 修改/修订/废止
    (
        re.compile(
            r"(?:修改|修订|废止|删除)\s*"
            r"(?:《)?" + _CN_LAW_NAME + r"(?:》)?"
            r"第(\d+)条"
        ),
        "amends",
        "regex",
    ),
    # CN: 废止文号 — pattern A: 废止 + 文号
    (
        re.compile(r"(?:废止|失效|停止执行)\s*(" + _CN_WENHAO + r")"),
        "supersedes",
        "regex",
    ),
    # CN: 废止文号 — pattern B: 文号 + 废止
    (
        re.compile(
            r"(" + _CN_WENHAO + r")"
            r"[^。；\n]{0,20}(?:已经|已|予以)?\s*(?:废止|失效|停止执行)"
        ),
        "supersedes",
        "regex",
    ),
    # CN: 法律条文引用 (with or without 书名号《》)
    (
        re.compile(
            r"(?:《)?"
            r"(" + _CN_LAW_NAME + r")"
            r"(?:》)?"
            r"第(\d+)条"
            r"(?:第(\d+)款)?"
        ),
        "interprets",
        "regex",
    ),
    # CN: 文号引用 — 财税〔2026〕X号 / 公告
    (
        re.compile(r"(" + _CN_WENHAO + r")"),
        "interprets",
        "regex",
    ),
    # CN: 子母法 — 实施条例/实施细则 引用母法
    (
        re.compile(
            r"(?:《)?"
            r"([一-鿿]{2,15}法)"
            r"(?:》)?"
            r"\s*(?:及其|和)\s*"
            r"(?:《)?"
            r"([一-鿿]{2,15}(?:实施条例|实施细则))"
            r"(?:》)?"
        ),
        "authority_of",
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

            citations.append(
                Citation(
                    raw_text=raw,
                    entity_key=entity_key,
                    relation_type=relation_type,
                    confidence=1.0,
                    extracted_by=method,
                )
            )

    return citations


def _match_to_entity_key(match: re.Match, relation_type: str) -> str:
    """Convert regex match to a stable entity_key."""
    groups = match.groups()
    full = match.group(0)

    # TW 法律條文: groups = (law_name, article_no, item?, subitem?)
    if len(groups) >= 2 and re.search(r"第\s*\d+.*條", full):
        law_name = _clean_law_name(groups[0]) if groups[0] else ""
        article_no = re.sub(r"\s+", "", groups[1])
        if law_name:
            key = f"{law_name}#{article_no}"
            if len(groups) > 2 and groups[2]:
                key += f"#{groups[2]}"
            return key

    # CN 法律条文: 第X条 (Arabic numerals)
    if len(groups) >= 2 and re.search(r"第\d+条", full):
        law_name = _clean_law_name(groups[0]) if groups[0] else ""
        article_no = re.sub(r"\s+", "", groups[1])
        if law_name:
            law_name = law_name.strip("《》")
            key = f"{law_name}#{article_no}"
            if len(groups) > 2 and groups[2]:
                key += f"#{groups[2]}"
            return key

    # CN 子母法: 法 及其 实施条例/实施细则
    if re.search(r"及其|和", full) and re.search(r"实施条例|实施细则", full):
        parts = [re.sub(r"\s+", "", g) for g in groups if g]
        return "+".join(parts)

    # TW 函釋: 台財稅字第XXXXX號
    ruling_match = re.search(r"((?:台|臺)財稅(?:發|字)?第\s*[\d]+\s*號)", full)
    if ruling_match:
        return re.sub(r"\s+", "", ruling_match.group(1)).replace("臺", "台")

    # 釋字/憲判字
    interp_match = re.search(r"((?:釋字|憲判字)第\s*\d+\s*號)", full)
    if interp_match:
        return re.sub(r"\s+", "", interp_match.group(1))

    # CN 文号
    wenhao_match = re.search(
        r"((?:国家税务总局|财政部\s*税务总局)公告\d{4}年第\d+号"
        r"|财税[〔\[]\d{4}[〕\]]\d+号"
        r"|国税[发函][〔\[]\d{4}[〕\]]\d+号"
        r"|税总[发函][〔\[]\d{4}[〕\]]\d+号)",
        full,
    )
    if wenhao_match:
        return re.sub(r"\s+", "", wenhao_match.group(1))

    return re.sub(r"\s+", "", full)


def _clean_law_name(name: str) -> str:
    """Strip leading verb prefixes that regex may capture before the actual law name."""
    prefixes = [
        "依據",
        "依据",
        "依照",
        "依",
        "按照",
        "按",
        "根据",
        "修正",
        "修改",
        "修订",
        "增訂",
        "刪除",
        "删除",
        "废止",
        "查",
    ]
    for p in sorted(prefixes, key=len, reverse=True):
        if name.startswith(p):
            name = name[len(p) :]
            break
    return name.strip()
