"""Parent-child law hierarchy (子母法).

An implementing regulation is not a document that merely *resembles* its parent
statute — it is the statute's operative detail. 增值税法实施条例第3条 defines what
增值税法第1条 means; read apart, both are incomplete. So the two must be one
subtree in the graph, not two peers in a flat list.

Two independent ways to establish the link, because either alone leaves gaps:

1. **Title derivation** (this module). 中华人民共和国增值税法实施条例 announces its
   parent in its own name. Cheap, exact, and works before a single provision is
   parsed — which matters because a child whose body failed to parse would
   otherwise float free.

2. **Citation extraction** (:mod:`taxwatch.graph.citation`). Picks up parents a
   title can't reveal: 營利事業所得稅查核準則 is authorised by 所得稅法 but says so
   only in its 第1條. It also supplies the article-level edges — 实施条例第3条 →
   税法第1条 — that make "which provisions does this change actually touch"
   answerable.

Both write ``AUTHORITY_OF`` edges pointing child → parent, so the existing
impact-spread traversal (which walks ``to_entity_id`` backwards) reaches every
descendant of a changed statute without modification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from taxwatch.models import (
    Document,
    ExtractionMethod,
    LegalEntity,
    LegalRelation,
    RelationType,
)

# Suffixes that mark a document as implementing detail for a parent named by
# the remaining prefix. Ordered longest-first so 暂行条例实施细则 is not read as
# 实施细则 hanging off a nonexistent 暂行条例-less parent.
_CHILD_SUFFIXES = (
    "實施條例",
    "实施条例",
    "實施細則",
    "实施细则",
    "實施辦法",
    "实施办法",
    "施行細則",
    "施行细则",
    "施行條例",
    "施行条例",
    "稽徵細則",
    "稽征细则",
)

# A derived parent is only believable if it reads like a statute in its own
# right. 「查核準則」 minus 「準則」 is not a law, and inventing that node would
# pollute the graph with entities nothing else ever references.
_PARENT_SUFFIX_RE = re.compile(
    r"(?:法|條例|条例|通則|通则|暫行條例|暂行条例)$",
)

# Deictic references a child uses for its own parent: 本法第14條, 税法第一条.
_PARENT_ALIASES = ("本法", "税法", "稅法", "母法")

# ...and for itself.
_SELF_ALIASES = ("本條例", "本条例", "本細則", "本细则", "本辦法", "本办法", "本準則", "本准则")


def derive_parent_key(entity_key: str) -> str | None:
    """The parent law implied by a child's own name, if its name implies one.

    >>> derive_parent_key("增值税法实施条例")
    '增值税法'
    >>> derive_parent_key("所得稅法施行細則")
    '所得稅法'
    >>> derive_parent_key("營利事業所得稅查核準則") is None
    True
    """
    key = entity_key.split("#", 1)[0].strip()
    for suffix in _CHILD_SUFFIXES:
        if not key.endswith(suffix) or len(key) <= len(suffix):
            continue
        parent = key[: -len(suffix)].strip()
        if _PARENT_SUFFIX_RE.search(parent):
            return parent
    return None


def is_child_of(child_key: str, parent_key: str) -> bool:
    return derive_parent_key(child_key) == parent_key.split("#", 1)[0]


_TITLE_STATUTE_RE = re.compile(
    r"《([一-鿿]{4,30}(?:法|條例|条例|暫行條例|暂行条例))》"
)

_CN_STATUTE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("增值税", "增值税法"),
    ("增值稅", "增值稅法"),
    ("消费税", "消费税法"),
    ("消費稅", "消費稅法"),
    ("企业所得税", "企业所得税法"),
    ("企業所得稅", "企業所得稅法"),
    ("个人所得税", "个人所得税法"),
    ("個人所得稅", "個人所得稅法"),
    ("契税", "契税法"),
    ("契稅", "契稅法"),
    ("印花税", "印花税法"),
    ("印花稅", "印花稅法"),
    ("车辆购置税", "车辆购置税法"),
    ("车船税", "车船税法"),
    ("资源税", "资源税法"),
    ("环境保护税", "环境保护税法"),
    ("城市维护建设税", "城市维护建设税法"),
    ("耕地占用税", "耕地占用税法"),
    ("烟叶税", "烟叶税法"),
    ("船舶吨税", "船舶吨税法"),
    ("土地增值税", "土地增值税暂行条例"),
    ("房产税", "房产税暂行条例"),
    ("城镇土地使用税", "城镇土地使用税暂行条例"),
    ("税收征收管理", "税收征收管理法"),
    ("税收征管", "税收征收管理法"),
)


def derive_parent_from_title(title: str, own_doc_type: str) -> str | None:
    if own_doc_type == "statute":
        return None
    for m in _TITLE_STATUTE_RE.finditer(title):
        return m.group(1)
    for keyword, statute in _CN_STATUTE_KEYWORDS:
        if keyword in title:
            return statute
    return None


def register_document_hierarchy(
    session: Session,
    document: Document,
    doc_entity_key: str,
) -> LegalRelation | None:
    """Give a document a node of its own, and wire it to its parent statute.

    Without this the graph only ever contains *article* nodes minted by citation
    extraction, so a document whose text cites nothing — or whose parent is
    named only in its title — is absent from the hierarchy entirely.
    """
    from taxwatch.graph.relations import upsert_relation
    from taxwatch.graph.resolver import resolve_entity

    entity = resolve_entity(session, doc_entity_key, title=document.title)
    if entity.current_document_id != document.id:
        entity.current_document_id = document.id

    parent_key = derive_parent_key(doc_entity_key)
    if not parent_key:
        parent_key = derive_parent_from_title(
            document.title, document.doc_type.value if hasattr(document.doc_type, "value") else str(document.doc_type)
        )
    if not parent_key:
        return None

    return upsert_relation(
        session,
        from_key=doc_entity_key,
        to_key=parent_key,
        relation_type=RelationType.AUTHORITY_OF,
        confidence=1.0,
        evidence_text=f"標題衍生：《{document.title}》為《{parent_key}》之施行法規",
        extracted_by=ExtractionMethod.REGEX,
    )


# A 依据 clause carries weight only where a document declares its authority —
# the opening provisions. Later on, 「根据……」 is ordinary argument, and
# promoting those would make every statute a parent of every 公告 citing it.
_AUTHORITY_SCAN_PROVISIONS = 3


def promote_declared_authority(
    session: Session,
    document: Document,
    doc_entity_key: str,
    provisions: list[Any],
) -> list[LegalRelation]:
    """Attach a 公告/规定 to the statute its opening clause names.

    公告 and 部门规章 don't announce their parent in their title the way an
    实施条例 does — 「国家税务总局关于电池消费税征收管理有关事项的公告」 names a
    topic, not a law. What they do have is 第1條: 「根据《消费税法》…的规定，现将…
    公告如下」. That clause is the hierarchy edge, and it lives in the text.

    Citation extraction already finds it, but records it against the *provision*
    (公告#1 → 消费税法). The document itself stays an orphan until that authority
    is promoted to document level, which is what this does.
    """
    from taxwatch.graph.citation import extract_citations
    from taxwatch.graph.relations import upsert_relation
    from taxwatch.graph.resolver import normalize_entity_key

    self_key = normalize_entity_key(doc_entity_key)
    relations: list[LegalRelation] = []
    seen: set[str] = set()

    for prov in provisions[:_AUTHORITY_SCAN_PROVISIONS]:
        for cit in extract_citations(prov.text, self_key=self_key):
            if cit.relation_type != "authority_of":
                continue
            # The authority is the law, not the article — a 公告 issued under
            # 消费税法第4条 still sits under 消费税法 in the hierarchy.
            parent_key = normalize_entity_key(cit.entity_key).split("#", 1)[0]
            if not parent_key or parent_key == self_key or parent_key in seen:
                continue
            seen.add(parent_key)
            relations.append(
                upsert_relation(
                    session,
                    from_key=self_key,
                    to_key=parent_key,
                    relation_type=RelationType.AUTHORITY_OF,
                    # Lower than a title-derived link: an opening clause can
                    # name several statutes, and only one is the true parent.
                    confidence=0.8,
                    evidence_text=cit.raw_text,
                    extracted_by=ExtractionMethod.REGEX,
                )
            )

    return relations


def parent_aliases(parent_key: str | None) -> tuple[str, ...]:
    """Deictic names a child document may use for `parent_key`."""
    return _PARENT_ALIASES if parent_key else ()


def self_aliases() -> tuple[str, ...]:
    return _SELF_ALIASES


# ---------- presentation ----------


@dataclass
class HierarchyNode:
    """A document plus the implementing regulations hanging off it."""

    document: dict[str, Any]
    children: list[HierarchyNode] = field(default_factory=list)

    @property
    def descendant_count(self) -> int:
        return sum(1 + c.descendant_count for c in self.children)


def build_forest(
    documents: list[dict[str, Any]],
    *,
    key_of: Any = None,
) -> list[HierarchyNode]:
    """Nest a flat document list into parent → child trees.

    Only links a child to a parent that is *present in the same list* — a
    dangling reference to a statute we don't monitor would otherwise silently
    hide the child from the view it belongs to.
    """
    key_of = key_of or (lambda d: entity_key_for_title(d.get("title", "")))

    nodes = {key_of(d): HierarchyNode(document=d) for d in documents}
    roots: list[HierarchyNode] = []

    for key, node in nodes.items():
        parent_key = derive_parent_key(key)
        parent = nodes.get(parent_key) if parent_key else None
        if parent is None:
            title_parent = derive_parent_from_title(
                node.document.get("title", ""),
                node.document.get("doc_type", ""),
            )
            if title_parent:
                from taxwatch.graph.resolver import normalize_entity_key
                parent = nodes.get(normalize_entity_key(title_parent))
        if parent is not None and parent is not node:
            parent.children.append(node)
        else:
            roots.append(node)

    for node in nodes.values():
        node.children.sort(key=lambda n: n.document.get("title", ""))
    roots.sort(key=lambda n: n.document.get("title", ""))
    return roots


def flatten_forest(roots: list[HierarchyNode], depth: int = 0) -> list[dict[str, Any]]:
    """Depth-annotated pre-order walk, for templates that render a flat table."""
    rows: list[dict[str, Any]] = []
    for node in roots:
        rows.append(
            {
                **node.document,
                "depth": depth,
                "child_count": len(node.children),
            }
        )
        rows.extend(flatten_forest(node.children, depth + 1))
    return rows


def entity_key_for_title(title: str) -> str:
    """The graph key a document title maps to.

    Must agree with the node keys the normalizers emit, or titles and provisions
    end up as separate nodes for the same law.
    """
    from taxwatch.graph.resolver import normalize_entity_key

    return normalize_entity_key(title)


def get_family(session: Session, entity_key: str) -> dict[str, list[LegalEntity]]:
    """The parent statute and sibling/child regulations around one entity."""
    from taxwatch.graph.resolver import normalize_entity_key

    key = normalize_entity_key(entity_key).split("#", 1)[0]
    entity = session.query(LegalEntity).filter_by(entity_key=key).first()
    if entity is None:
        return {"parents": [], "children": []}

    parents = (
        session.query(LegalEntity)
        .join(LegalRelation, LegalRelation.to_entity_id == LegalEntity.id)
        .filter(
            LegalRelation.from_entity_id == entity.id,
            LegalRelation.relation_type == RelationType.AUTHORITY_OF,
        )
        .all()
    )
    children = (
        session.query(LegalEntity)
        .join(LegalRelation, LegalRelation.from_entity_id == LegalEntity.id)
        .filter(
            LegalRelation.to_entity_id == entity.id,
            LegalRelation.relation_type == RelationType.AUTHORITY_OF,
        )
        .all()
    )
    # Article-level nodes are noise in a document-level family view.
    return {
        "parents": [e for e in parents if "#" not in e.entity_key],
        "children": [e for e in children if "#" not in e.entity_key],
    }
