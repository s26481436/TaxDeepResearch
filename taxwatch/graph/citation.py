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
# 规定/规程 matter here because 部门规章 like 税务人员税收业务违法行为处分规定
# are named that way; without them the 依据 clause naming their parent is
# unparseable and the document floats free of the hierarchy.
_CN_LAW_SUFFIX = r"(?:法|条例|细则|办法|规则|规定|规程|暂行条例|暂行办法|管理办法)"
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
    # CN: 依据整部法律 — 公告/规定 rarely cite an article, they just name the
    # statutes they were made under: 根据《公务员法》《税收征收管理法》，制定本规定。
    # This is the clause that puts a 公告 in the hierarchy at all. Several may be
    # named in a row, with or without a separator, and each is an authority.
    _Rule(
        re.compile(
            r"(?:依据|依照|按照|根据)\s*"
            r"((?:《[一-鿿]{4,30}" + _CN_LAW_SUFFIX + r"》[\s、和及]*)+)"
        ),
        "authority_of",
        "law_whole_run",
    ),
    # TW: 依據整部法律 — 「依所得稅法規定」訂定
    _Rule(
        re.compile(r"(?:依|依據|按|按照)\s*(" + _LAW_NAME + r")\s*(?:第\s*\d+\s*條)?\s*規定"),
        "authority_of",
        "law_whole_authority",
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



# 第66條之9 and 第66之9條 are the same provision written two ways. The rules
# below all expect the sub-article *before* 條, which is the form the MOJ JSON
# uses; normalising the other form once here beats threading an optional group
# through every pattern and shifting their group indices.
_SUFFIX_SUB_ARTICLE = re.compile(
    rf"第\s*(\d+|[{CN_NUMERAL_CHARS}]{{1,8}})\s*[條条]\s*之\s*(\d+|[{CN_NUMERAL_CHARS}]{{1,3}})"
)

# 「所得稅法第88條、第92條」 names two provisions of one law. Only the first
# carries the law name, so the trailing items are invisible to the rules —
# and a filing requirement that rests on two articles would silently keep one.
_ARTICLE_RUN = re.compile(
    rf"[、,，及和與]\s*第\s*(\d+(?:-\d+)?(?:之\d+)?|[{CN_NUMERAL_CHARS}]{{1,8}}(?:之[{CN_NUMERAL_CHARS}]{{1,3}})?)\s*[條条]"
)


def _normalise_sub_articles(text: str) -> str:
    """第66條之9 -> 第66之9條, so one form reaches the rules."""
    return _SUFFIX_SUB_ARTICLE.sub(r"第\1之\2條", text)


def _article_run_keys(text: str, law_name: str, end: int) -> list[tuple[str, str]]:
    """Extra provisions listed after a law+article match, as (key, raw_text)."""
    keys: list[tuple[str, str]] = []
    position = end
    while True:
        match = _ARTICLE_RUN.match(text, position)
        if match is None:
            break
        keys.append((_article_key(law_name, match.group(1)), match.group(0).strip()))
        position = match.end()
    return keys


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
    text = _normalise_sub_articles(text)

    citations: list[Citation] = []
    seen: set[str] = set()

    def _add(entity_key: str, raw_text: str, relation_type: str) -> None:
        dedup_key = f"{entity_key}:{relation_type}"
        if dedup_key in seen:
            return
        seen.add(dedup_key)
        citations.append(
            Citation(
                raw_text=raw_text,
                entity_key=entity_key,
                relation_type=relation_type,
                confidence=1.0,
                extracted_by="regex",
            )
        )

    for rule in _RULES:
        for match in rule.pattern.finditer(text):
            for entity_key in _entity_keys(match, rule, parent_key=parent_key, self_key=self_key):
                _add(entity_key, match.group(0).strip(), rule.relation_type)

                if "#" in entity_key:
                    law_name = entity_key.split("#", 1)[0]
                    for extra_key, extra_raw in _article_run_keys(text, law_name, match.end()):
                        _add(extra_key, extra_raw, rule.relation_type)

    return citations


def _entity_keys(
    match: re.Match,
    rule: _Rule,
    *,
    parent_key: str | None,
    self_key: str | None,
) -> list[str]:
    """The node keys this match points at — usually one, empty to drop it."""
    if rule.kind == "law_whole_run":
        # 根据《A法》《B法》 names every one of them as an authority.
        keys = []
        for name in re.findall(r"《([^》]+)》", match.group(1)):
            cleaned = _clean_law_name(name).strip()
            if cleaned and cleaned not in _DEICTIC_STEMS:
                keys.append(cleaned)
        return keys

    key = _entity_key(match, rule, parent_key=parent_key, self_key=self_key)
    return [key] if key else []


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

    if rule.kind in ("law_whole", "law_whole_authority"):
        raw = next((g for g in match.groups() if g), "")
        name = _clean_law_name(raw).strip("《》")
        return name if name and name not in _DEICTIC_STEMS else None

    # ruling / interpretation / wenhao — the match is already the identifier.
    return re.sub(r"\s+", "", match.group(1)).replace("臺", "台")


def _article_key(law_name: str | None, article: str) -> str:
    """`所得稅法` + `十四` -> `所得稅法#14`.

    Sub-articles normalise to a hyphen, not 之: the TW normalizer mints
    `所得稅法#66-9` from 「第 66-9 條」(`tw_law_json._build_node_key`), so a
    citation emitting `66之9` would name a node nothing else can reach —
    and 所得稅法第66條之9 (未分配盈餘) is exactly the kind of provision the
    filing matrix cites.
    """
    base = (law_name or "").split("#", 1)[0]
    number = re.sub(r"\s+", "", article)
    # 第十四條之一 / 第14條之1 -> 14-1
    if "之" in number:
        head, _, tail = number.partition("之")
        number = f"{to_arabic(head)}-{to_arabic(tail)}"
    else:
        number = to_arabic(number)
    return f"{base}#{number}"


_NAME_PREFIXES = (
    "依據",
    "依据",
    "依照",
    "依",
    "按照",
    "按",
    "根据",
    "根據",
    "修正",
    "修改",
    "修订",
    "增訂",
    "刪除",
    "删除",
    "废止",
    "查",
)

# A law name must end in a law-ish suffix; used to check that trimming left
# something that is still a law rather than a fragment.
_LAW_NAME_SHAPE = re.compile(
    r"^[一-鿿]{2,20}(?:法|條例|条例|準則|准则|辦法|办法|規則|规则|細則|细则|規定|规定)$"
)


def _clean_law_name(name: str) -> str:
    """Trim the verbs a greedy law-name match drags in front of the real name.

    Two cases. A leading verb (依所得稅法) is simply dropped. Harder: the match
    can start inside the *citing* document's self-reference, as in
    「本準則依所得稅法第80條」 where the capture is 本準則依所得稅法 — left alone
    that mints a node named after two laws at once. Cutting at the embedded
    authority verb recovers 所得稅法, but only when what remains still reads as
    a law name, so an ordinary name that happens to contain 依 survives intact.
    """
    name = name.strip()

    for prefix in sorted(_NAME_PREFIXES, key=len, reverse=True):
        if name.startswith(prefix):
            return name[len(prefix) :].strip()

    for marker in ("依據", "依据", "依照", "根據", "根据", "按照", "依", "按"):
        _, sep, tail = name.rpartition(marker)
        if sep and _LAW_NAME_SHAPE.match(tail.strip()):
            return tail.strip()

    return name
