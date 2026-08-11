"""文號 (document number) extraction — the stable identity of a CN tax document.

Chinese tax documents are cited by 文號 rather than by title, so this is the
join key between our crawled documents, citations found in provision text, and
any reference corpus.

Two things make naive patterns fail:

- The brackets are full-width 〔〕 in official text, not ASCII [].
- Prefixes are open-ended: 财税, 税总发, 税总函, 税总办发, 国税函发, 财关税,
  and joint issuances that name several ministries before 公告.
"""

from __future__ import annotations

import re

# Bracketed serial form: 财税〔2026〕15号
_YEAR_BRACKET = r"[〔\[（(]\s*\d{4}\s*[〕\]）)]\s*第?\s*\d+\s*号"

# Announcement form: 国家税务总局公告2026年第6号
_YEAR_NO = r"\d{4}\s*年\s*第\s*\d+\s*号"

# An issuing body's name never begins with a citation connector or lead-in
# verb. Without excluding them the org pattern absorbs the preceding word:
# 「和国家税务总局公告…」 would match from 和, 「根据国家税务总局公告…」 from 根.
# No real ministry starts with any of these. Note 发 is deliberately absent:
# 发展改革委 is a real issuing body.
_NOT_ORG_HEAD = "和及与根据依照按见参详本该其等的了并或另如废止适用修订印执转引布施续现自将"
# 新 (新疆, 新闻出版署), 经 (经济和信息化委员会) and 原 are deliberately absent:
# they head real issuing bodies and abbreviations.

# 委 and 院 are needed for 国家发展改革委 and 国务院, which promulgate
# 行政法规; without them a joint issuance breaks apart mid-list.
_ORG_SUFFIX = r"(?:部|局|署|委员会|委|院|总局|银行|办公厅)"
_ORG = rf"[^{_NOT_ORG_HEAD}\W\d_][一-鿿]{{1,11}}{_ORG_SUFFIX}"
_ORG_CONT = rf"[一-鿿]{{2,12}}{_ORG_SUFFIX}"
# A separator is required between co-issuers, and the list is bounded. Both
# matter for speed, not just correctness: an optional separator plus an
# unbounded `*` around a `{2,12}` run backtracks catastrophically on long
# documents, and these are scanned over full 100k-character provisions.
_ORG_LIST = rf"(?:中华人民共和国)?{_ORG}(?:(?:\s*[、和]\s*|\s+){_ORG_CONT}){{0,6}}"

_PATTERNS: tuple[str, ...] = (
    # Announcements, including joint issuances that list several departments
    # before 公告 (财政部 海关总署 税务总局公告2025年第256号).
    rf"{_ORG_LIST}\s*(?:公告|通告)\s*(?:{_YEAR_NO}|{_YEAR_BRACKET})",
    # 主席令 — how 法律 are promulgated: 中华人民共和国主席令第7号
    r"(?:中华人民共和国)?主席令第?\s*\d+\s*号",
    # 令 (ministerial order), also possibly joint: 国家税务总局 财政部令第18号
    rf"{_ORG_LIST}\s*令\s*(?:{_YEAR_NO}|第?\s*\d+\s*号)",
    # Bracketed serials, longest prefixes first so 国税函发 wins over 国税函.
    rf"(?:财税字|财税|财关税|财综|财企|财预|财会){_YEAR_BRACKET}",
    rf"(?:税总办发|税总发|税总函|税总){_YEAR_BRACKET}",
    rf"(?:国税函发|国税发|国税函|国税地字|国税外){_YEAR_BRACKET}",
    rf"(?:国办发|国发){_YEAR_BRACKET}",
)

# Characters that introduce a citation rather than belonging to the issuing
# body's name. Without excluding them the generic pattern below swallows the
# lead-in: 「根据财税〔2026〕15号」 would match from 根 rather than from 财.
_LEAD_IN = "根据依照按见参如和与及第本条本该其等的了在于对为以时并或者另详另行同时上述前款"

# Other ministries cited inside tax documents: 人社部发〔2024〕3号,
# 发改高技〔2023〕1号. Applied only where no specific pattern matched.
_GENERIC = re.compile(rf"[^{_LEAD_IN}\W\d_]{{1,7}}[一-鿿](?:发|函|字|规){_YEAR_BRACKET}")

_COMPILED = [re.compile(p) for p in _PATTERNS]
_SPECIFIC_ANY = re.compile("|".join(_PATTERNS))


def _scan(text: str) -> list[tuple[int, int, str]]:
    """All 文號 spans in `text`, specific patterns taking precedence.

    Specific prefixes are matched first across the whole string; the generic
    ministry pattern then fills only the gaps. Running both in one alternation
    lets the looser pattern win purely by starting earlier, which is how the
    lead-in-swallowing bug arose.
    """
    spans: list[tuple[int, int, str]] = [
        (m.start(), m.end(), m.group(0)) for m in _SPECIFIC_ANY.finditer(text)
    ]
    for m in _GENERIC.finditer(text):
        if not any(start < m.end() and m.start() < end for start, end, _ in spans):
            spans.append((m.start(), m.end(), m.group(0)))
    spans.sort(key=lambda s: (s[0], -s[1]))
    return spans


def normalize(wenhao: str) -> str:
    """Canonical form for use as a lookup key.

    Collapses whitespace and folds the several bracket styles onto 〔〕 so that
    `财税[2026]15号`, `财税（2026）15号` and `财税〔2026〕15 号` all agree.
    """
    if not wenhao:
        return ""
    # Government exports frequently carry a BOM or zero-width joiner.
    text = wenhao.replace("﻿", "").replace("​", "")
    text = re.sub(r"\s+", "", text)
    text = text.translate(
        str.maketrans({"[": "〔", "]": "〕", "（": "〔", "）": "〕", "(": "〔", ")": "〕"})
    )
    return text


def extract_first(text: str) -> str | None:
    """The leftmost 文號 in `text`, normalized — or None.

    A joint issuance like 「海关总署 税务总局公告2025年第256号」 is captured
    whole rather than matching only from 税务总局 onwards.
    """
    spans = _scan(text or "")
    return normalize(spans[0][2]) if spans else None


def extract_all(text: str) -> list[str]:
    """Every distinct 文號 in `text`, normalized, in order of appearance."""
    seen: set[str] = set()
    found: list[str] = []
    last_end = -1
    for start, end, raw in _scan(text or ""):
        if start < last_end:  # overlapping shorter match
            continue
        last_end = end
        key = normalize(raw)
        if key and key not in seen:
            seen.add(key)
            found.append(key)
    return found
