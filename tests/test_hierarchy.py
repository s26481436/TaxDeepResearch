"""Tests for 子母法 (parent-child law) linkage.

The case that motivated this: 增值税法 and 增值税法实施条例 were stored as two
unrelated documents, so a change to the statute never surfaced the regulation
that spells it out.
"""

from __future__ import annotations

import pytest

from taxwatch.cn_numerals import to_arabic, to_int
from taxwatch.graph.citation import extract_citations
from taxwatch.graph.hierarchy import (
    build_forest,
    derive_parent_key,
    flatten_forest,
    get_family,
    register_document_hierarchy,
)
from taxwatch.graph.relations import get_entity_context
from taxwatch.graph.resolver import normalize_entity_key
from taxwatch.models import DocType, Document, Source

# ---------------------------------------------------------------------------
# Chinese numerals
# ---------------------------------------------------------------------------


class TestCnNumerals:
    @pytest.mark.parametrize(
        "cn,expected",
        [
            ("一", 1),
            ("十", 10),
            ("十一", 11),
            ("二十三", 23),
            ("五十一", 51),
            ("一百", 100),
            ("一百三十三", 133),
            ("二百五十", 250),
            ("〇", 0),
        ],
    )
    def test_converts(self, cn, expected):
        assert to_int(cn) == expected

    @pytest.mark.parametrize("bad", ["", "abc", "第一条", "12"])
    def test_rejects_non_numerals(self, bad):
        assert to_int(bad) is None

    def test_to_arabic_passes_through_unconvertible(self):
        """A weird heading should still key a provision, just not a tidy one."""
        assert to_arabic("甲") == "甲"


# ---------------------------------------------------------------------------
# Title-derived parents
# ---------------------------------------------------------------------------


class TestDeriveParentKey:
    @pytest.mark.parametrize(
        "child,parent",
        [
            ("增值税法实施条例", "增值税法"),
            ("企业所得税法实施条例", "企业所得税法"),
            ("税收征收管理法实施细则", "税收征收管理法"),
            ("所得稅法施行細則", "所得稅法"),
            ("加值型及非加值型營業稅法施行細則", "加值型及非加值型營業稅法"),
            ("房屋稅條例施行細則", "房屋稅條例"),
            ("消费税暂行条例实施细则", "消费税暂行条例"),
        ],
    )
    def test_derives(self, child, parent):
        assert derive_parent_key(child) == parent

    @pytest.mark.parametrize(
        "standalone",
        [
            "增值税法",
            "所得稅法",
            # 準則 names no parent — its authority is declared in 第1條 instead,
            # and citation extraction is what picks that up.
            "營利事業所得稅查核準則",
            # Stripping the suffix would leave 「營業」, which is not a law.
            "營業實施細則",
        ],
    )
    def test_returns_none_when_no_parent_implied(self, standalone):
        assert derive_parent_key(standalone) is None

    def test_ignores_article_suffix(self):
        assert derive_parent_key("增值税法实施条例#3") == "增值税法"


class TestNormalizeEntityKey:
    def test_strips_prc_prefix_so_parent_and_child_meet(self):
        child = normalize_entity_key("中华人民共和国增值税法实施条例")
        assert child == "增值税法实施条例"
        assert derive_parent_key(child) == normalize_entity_key("中华人民共和国增值税法")

    def test_keeps_prc_when_not_a_prefix(self):
        key = "中华人民共和国政府和新加坡共和国政府税收协定"
        assert normalize_entity_key(key).startswith("政府和新加坡")

    def test_strips_book_title_marks(self):
        assert normalize_entity_key("《增值税法》") == "增值税法"


# ---------------------------------------------------------------------------
# Citations that carry the linkage
# ---------------------------------------------------------------------------


class TestChildCitesParent:
    def test_cn_chinese_numeral_article(self):
        """CN statutes write 第一条, not 第1条 — matching only Arabic found nothing."""
        cites = extract_citations("企业所得税法第一百三十三条规定如下。")
        assert "企业所得税法#133" in {c.entity_key for c in cites}

    def test_cn_deictic_parent_reference(self):
        """实施条例 says 「税法第一条」 after defining the shorthand in Article 1."""
        cites = extract_citations(
            "税法第一条所称在中华人民共和国境内销售货物，是指起运地在境内。",
            parent_key="增值税法",
        )
        assert ("增值税法#1", "authority_of") in {(c.entity_key, c.relation_type) for c in cites}

    def test_tw_deictic_parent_reference(self):
        cites = extract_citations(
            "本法第14條所稱財產租賃所得，指下列各項。",
            parent_key="所得稅法",
        )
        assert "所得稅法#14" in {c.entity_key for c in cites}

    def test_deictic_dropped_without_parent_context(self):
        """Better no edge than a 「本法」 node every document in the corpus shares."""
        cites = extract_citations("本法第14條所稱財產租賃所得。")
        assert not [c for c in cites if "本法" in c.entity_key]

    def test_prefixed_deictic_does_not_leak_a_bogus_node(self):
        cites = extract_citations("依本法第88條規定辦理扣繳。", parent_key="所得稅法")
        keys = {c.entity_key for c in cites}
        assert keys == {"所得稅法#88"}

    def test_self_reference_uses_own_key(self):
        cites = extract_citations(
            "本条例第三条所称销售额。",
            self_key="增值税法实施条例",
        )
        assert "增值税法实施条例#3" in {c.entity_key for c in cites}

    def test_family_phrase_yields_the_parent_not_a_joined_key(self):
        """《X法》及其《X法实施条例》 used to mint a single 'X法+X法实施条例' node."""
        cites = extract_citations(
            "《中华人民共和国增值税法》及其《中华人民共和国增值税法实施条例》"
        )
        authority = [c for c in cites if c.relation_type == "authority_of"]
        assert [c.entity_key for c in authority] == ["中华人民共和国增值税法"]


# ---------------------------------------------------------------------------
# Graph registration
# ---------------------------------------------------------------------------


def _document(session, title: str, doc_type: DocType = DocType.REGULATION) -> Document:
    source = session.query(Source).filter_by(key="cn-chinatax").first()
    if source is None:
        source = Source(key="cn-chinatax", country="CN", connector="cn_chinatax")
        session.add(source)
        session.flush()
    doc = Document(
        source_id=source.id,
        external_id=title,
        doc_type=doc_type,
        title=title,
    )
    session.add(doc)
    session.flush()
    return doc


class TestRegisterDocumentHierarchy:
    def test_links_child_to_parent(self, session):
        parent = _document(session, "增值税法", DocType.STATUTE)
        child = _document(session, "增值税法实施条例")
        register_document_hierarchy(session, parent, "增值税法")
        register_document_hierarchy(session, child, "增值税法实施条例")
        session.flush()

        family = get_family(session, "增值税法")
        assert [e.entity_key for e in family["children"]] == ["增值税法实施条例"]

        child_family = get_family(session, "增值税法实施条例")
        assert [e.entity_key for e in child_family["parents"]] == ["增值税法"]

    def test_document_without_parent_gets_a_node_anyway(self, session):
        doc = _document(session, "增值税法", DocType.STATUTE)
        assert register_document_hierarchy(session, doc, "增值税法") is None
        session.flush()

        family = get_family(session, "增值税法")
        assert family == {"parents": [], "children": []}

    def test_entity_points_back_at_the_document(self, session):
        doc = _document(session, "增值税法实施条例")
        register_document_hierarchy(session, doc, "增值税法实施条例")
        session.flush()

        from taxwatch.models import LegalEntity

        entity = session.query(LegalEntity).filter_by(entity_key="增值税法实施条例").one()
        assert entity.current_document_id == doc.id

    def test_article_change_inherits_its_document_hierarchy(self, session):
        """A change lands on 实施条例#3; the 母法 must still be reachable from it."""
        parent = _document(session, "增值税法", DocType.STATUTE)
        child = _document(session, "增值税法实施条例")
        register_document_hierarchy(session, parent, "增值税法")
        register_document_hierarchy(session, child, "增值税法实施条例")

        from taxwatch.graph.resolver import resolve_entity

        resolve_entity(session, "增值税法实施条例#3")
        session.flush()

        ctx = get_entity_context(session, "增值税法实施条例#3")
        assert [e.entity_key for e in ctx["parent_documents"]] == ["增值税法"]


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------


class TestBuildForest:
    def _docs(self, *titles):
        return [{"title": t, "external_id": t} for t in titles]

    def test_nests_child_under_parent(self):
        forest = build_forest(self._docs("增值税法", "增值税法实施条例"))
        assert len(forest) == 1
        root = forest[0]
        assert root.document["title"] == "增值税法"
        assert [c.document["title"] for c in root.children] == ["增值税法实施条例"]

    def test_orphan_child_stays_visible_as_a_root(self):
        """We may monitor the regulation without monitoring its statute."""
        forest = build_forest(self._docs("增值税法实施条例"))
        assert [n.document["title"] for n in forest] == ["增值税法实施条例"]

    def test_unrelated_documents_are_siblings(self):
        forest = build_forest(self._docs("增值税法", "消费税法"))
        assert len(forest) == 2

    def test_flatten_annotates_depth_and_child_count(self):
        rows = flatten_forest(build_forest(self._docs("增值税法", "增值税法实施条例")))
        assert [(r["title"], r["depth"]) for r in rows] == [
            ("增值税法", 0),
            ("增值税法实施条例", 1),
        ]
        assert rows[0]["child_count"] == 1
        assert rows[1]["child_count"] == 0

    def test_matches_across_the_prc_prefix(self):
        forest = build_forest(
            self._docs("中华人民共和国增值税法", "中华人民共和国增值税法实施条例")
        )
        assert len(forest) == 1
        assert len(forest[0].children) == 1
