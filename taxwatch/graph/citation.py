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

Article numbers may be written either way — 第28条 or 第二十八条 — and Chinese
numerals dominate in CN statutes, so both are matched and normalised to Arabic
to line up with the node keys the normalizers emit.

Implementing regulations name their parent deictically (本法第14條, 税法第一条)
after defining the shorthand once in Article 1. Those references carry the
子母法 linkage, so callers that know a document's parent pass `parent_key` and
get real edges instead of a 「本法」 node nothing else can reach.

Regex covers the majority of cases; LLM fallback handles free-form references.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from taxwatch.cn_numerals import CN_NUMERAL_CHARS, to_arabic


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
_TW_ART = rf"(\d+(?:-\d+)?(?:之\d+)?|[{CN_NUMERAL_CHARS}]{{1,8}}(?:之[{CN_NUMERAL_CHARS}]{{1,3}})?)"

# --- CN patterns ---
_CN_LAW_SUFFIX = r"(?:法|条例|细则|办法|规则|暂行条例|暂行办法|管理办法)"
_CN_LAW_NAME = rf"(?:[一-鿿]{{2,20}}{_CN_LAW_SUFFIX})"
# 第28条 or 第二十八条 — CN statutes overwhelmingly use the latter.
_CN_ART = rf"(\d+|[{CN_NUMERAL_CHARS}]{{1,8}})"
_CN_WENHAO_CAISHUI = r"财税[〔\[]\d{4}[〕\]]\d+号"
_CN_WENHAO_GONGGAO = r"(?:国家税务总局|财政部\s*税务总局)公告\d{4}年第\d+号"
_CN_WENHAO_GUOSHUI = r"国税[发函][〔\[]\d{4}[〕\]]\d+号"
_CN_WENHAO_SHUIZONG = r"税总[发函][〔\[]\d{4}[〕\]]\d+号"
_CN_WENHAO = (
    rf"(?:{_CN_WENHAO_GONGGAO}|{_CN_WENHAO_CAISHUI}"
    rf"|{_CN_WENHAO_GUOSHUI}|{_CN_WENHAO_SHUIZONG})"
)

# Shorthand a document uses for its own parent (本法/税法) or for itself
# (本條例/本細則). Left as-is they become nodes named 「本法」 shared by every
# document in the corpus — worse than no edge at all, so the generic law-name
# rules refuse them and the deictic rules resolve them against real keys.
_PARENT_DEICTIC = r"(?:本法|税法|稅法|母法)"
_SELF_DEICTIC = r"(?:本條例|本条例|本細則|本细则|本辦法|本办法|本準則|本准则)"
_DEICTIC_STEMS = frozenset(
    {
        "本法",
        "税法",
        "稅法",
        "母法",
        "該法",
        "该法",
        "本條例",
        "本条例",
        "本細則",
        "本细则",
        "本辦法",
        "本办法",
        "本準則",
        "本准则",
    }
)


@dataclass(frozen=True)
class _Rule:
    pattern: re.Pattern
    relation_type: str
    kind: str


# Order matters: a rule that fires first claims the reading. 依據/授權 must
# precede the generic article reference so an authority clause is not demoted
# to a plain citation.
_RULES: list[_Rule] = [
    # --- TW: 依據/授權 ---
    _Rule(
        re.compile(r"(?:依|依據|按|按照)\s*(" + _LAW_NAME + r")第\s*" + _TW_ART + r"\s*條"),
        "authority_of",
        "law_article",
    ),
    # TW: 修正/修改
    _Rule(
        re.compile(r"(?:修正|修改|增訂|刪除)\s*(" + _LAW_NAME + r")第\s*" + _TW_ART + r"\s*條"),
        "amends",
        "law_article",
    ),
    # TW: 停止適用/廢止 — pattern A
    _Rule(
        re.compile(r"(?:停止適用|廢止|不再適用)\s*(" + _RULING_NUM + r")"),
        "supersedes",
        "ruling",
    ),
    # TW: 停止適用/廢止 — pattern B
    _Rule(
        re.compile(
            r"(" + _RULING_NUM + r")"
            r"[^。；\n]{0,20}(?:業經|已|應)?\s*(?:停止適用|廢止|不再適用)"
        ),
        "supersedes",
        "ruling",
    ),
    # --- TW/CN: 子法引用母法 (本法第X條) ---
    _Rule(
        re.compile(_PARENT_DEICTIC + r"第\s*" + _TW_ART + r"\s*[條条]"),
        "authority_of",
        "parent_deictic",
    ),
    # TW/CN: 子法引用自身 (本條例第X條)
    _Rule(
        re.compile(_SELF_DEICTIC + r"第\s*" + _TW_ART + r"\s*[條条]"),
        "cites",
        "self_deictic",
    ),
    # TW: 法律條文引用
    _Rule(
        re.compile(
            r"(" + _LAW_NAME + r")"
            r"第\s*" + _TW_ART + r"\s*條"
            r"(?:第\s*(\d+)\s*項)?"
            r"(?:第\s*(\d+)\s*(?:款|類|目))?"
        ),
        "interprets",
        "law_article",
    ),
    # TW: 財政部函釋
    _Rule(re.compile(r"(" + _RULING_NUM + r")"), "interprets", "ruling"),
    # TW: 釋字
    _Rule(re.compile(r"(釋字第\s*\d+\s*號)"), "interprets", "interpretation"),
    # TW: 憲判字
    _Rule(re.compile(r"(憲判字第\s*\d+\s*號)"), "interprets", "interpretation"),
    # --- CN: 子母法 — 《X法》及其《X法实施条例》 ---
    # Names both sides at once; the child derives its authority from the parent.
    _Rule(
        re.compile(
            r"(?:《)?([一-鿿]{2,15}法)(?:》)?"
            r"\s*(?:及其|和)\s*"
            r"(?:《)?([一-鿿]{2,15}(?:实施条例|实施细则|實施條例|實施細則))(?:》)?"
        ),
        "authority_of",
        "family",
    ),
    # --- CN: 依据/依照 (authority) ---
    _Rule(
        re.compile(
            r"(?:依据|依照|按照|根据)\s*"
            r"(?:《)?(" + _CN_LAW_NAME + r")(?:》)?"
            r"第" + _CN_ART + r"条"
        ),
        "authority_of",
        "law_article",
    ),
    # CN: 修改/修订/废止
    _Rule(
        re.compile(
            r"(?:修改|修订|废止|删除)\s*"
            r"(?:《)?(" + _CN_LAW_NAME + r")(?:》)?"
            r"第" + _CN_ART + r"条"
        ),
        "amends",
        "law_article",
    ),
    # CN: 废止文号 — pattern A: 废止 + 文号
    _Rule(
        re.compile(r"(?:废止|失效|停止执行)\s*(" + _CN_WENHAO + r")"),
        "supersedes",
        "wenhao",
    ),
    # CN: 废止文号 — pattern B: 文号 + 废止
    _Rule(
        re.compile(
            r"(" + _CN_WENHAO + r")"
            r"[^。；\n]{0,20}(?:已经|已|予以)?\s*(?:废止|失效|停止执行)"
        ),
        "supersedes",
        "wenhao",
    ),
    # CN: 法律条文引用 (with or without 书名号《》)
    _Rule(
        re.compile(
            r"(?:《)?(" + _CN_LAW_NAME + r")(?:》)?"
            r"第" + _CN_ART + r"条"
            r"(?:第(\d+)款)?"
        ),
        "interprets",
        "law_article",
    ),
    # CN: 文号引用 — 财税〔2026〕X号 / 公告
    _Rule(re.compile(r"(" + _CN_WENHAO + r")"), "interprets", "wenhao"),
    # CN: 引用整部法律 (no article number) — 《中华人民共和国增值税法》
    _Rule(
        re.compile(r"《([一-鿿]{4,30}" + _CN_LAW_SUFFIX + r")》"),
        "interprets",
        "law_whole",
    ),
]


def extract_citations(
    text: str,
    *,
    parent_key: str | None = None,
    self_key: str | None = None,
) -> list[Citation]:
    """Pull legal references out of one provision's text.

    `parent_key` and `self_key` let a child document's deictic references
    (本法第14條, 本條例第3條) resolve to real entities. Without them those
    references are dropped rather than turned into a shared 「本法」 node.
    """
    citations: list[Citation] = []
    seen: set[str] = set()

    for rule in _RULES:
        for match in rule.pattern.finditer(text):
            entity_key = _entity_key(match, rule, parent_key=parent_key, self_key=self_key)
            if not entity_key:
                continue

            dedup_key = f"{entity_key}:{rule.relation_type}"
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            citations.append(
                Citation(
                    raw_text=match.group(0).strip(),
                    entity_key=entity_key,
                    relation_type=rule.relation_type,
                    confidence=1.0,
                    extracted_by="regex",
                )
            )

    return citations


def _entity_key(
    match: re.Match,
    rule: _Rule,
    *,
    parent_key: str | None,
    self_key: str | None,
) -> str | None:
    """Build the node key this match points at, or None to drop the match."""
    if rule.kind == "parent_deictic":
        return _article_key(parent_key, match.group(1)) if parent_key else None

    if rule.kind == "self_deictic":
        return _article_key(self_key, match.group(1)) if self_key else None

    if rule.kind == "family":
        # Two named laws: the edge runs child → parent, so key on the parent.
        return _clean_law_name(match.group(1)).strip("《》")

    if rule.kind == "law_article":
        law_name = _clean_law_name(match.group(1) or "").strip("《》")
        if not law_name or law_name in _DEICTIC_STEMS:
            return None
        key = _article_key(law_name, match.group(2))
        # Optional 第X項 narrows the reference one level further.
        groups = match.groups()
        if len(groups) > 2 and groups[2]:
            key += f"#{groups[2]}"
        return key

    if rule.kind == "law_whole":
        name = _clean_law_name(match.group(1)).strip("《》")
        return name if name and name not in _DEICTIC_STEMS else None

    # ruling / interpretation / wenhao — the match is already the identifier.
    return re.sub(r"\s+", "", match.group(1)).replace("臺", "台")


def _article_key(law_name: str | None, article: str) -> str:
    """`所得稅法` + `十四` -> `所得稅法#14`."""
    base = (law_name or "").split("#", 1)[0]
    number = re.sub(r"\s+", "", article)
    # 第十四條之一 -> 14之1
    if "之" in number:
        head, _, tail = number.partition("之")
        number = f"{to_arabic(head)}之{to_arabic(tail)}"
    else:
        number = to_arabic(number)
    return f"{base}#{number}"


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
