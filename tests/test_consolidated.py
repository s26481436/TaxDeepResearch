"""Tests for the consolidated reading view and issue-date timelines.

Covers three reported gaps:
- 公告/规定 never attached to the statute they were issued under
- no way to read a statute together with what implements it
- every date shown was the crawl time, so the timeline collapsed onto today
"""

from __future__ import annotations

import hashlib
from datetime import datetime

import pytest

from taxwatch.graph.citation import extract_citations
from taxwatch.graph.hierarchy import (
    derive_parent_key,
    get_family,
    promote_declared_authority,
    register_document_hierarchy,
)
from taxwatch.graph.relations import store_citations
from taxwatch.models import DocType, Document, ProvisionNode, Snapshot, Source
from taxwatch.normalize.base import ProvisionData
from taxwatch.services import consolidated as svc
from taxwatch.services import documents as documents_svc

CRAWLED_AT = datetime(2026, 8, 11)


def _ingest(
    session,
    title: str,
    doc_type: DocType,
    issued_at: datetime | None,
    provisions: list[tuple[str, str]],
    *,
    fetched_at: datetime = CRAWLED_AT,
) -> Document:
    """Put one document through the same graph wiring the pipeline performs."""
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
        issued_at=issued_at,
    )
    session.add(doc)
    session.flush()

    snapshot = Snapshot(
        document_id=doc.id,
        content_hash=hashlib.sha256(title.encode()).hexdigest(),
        issued_at=issued_at,
        fetched_at=fetched_at,
    )
    session.add(snapshot)
    session.flush()

    doc_key = title.replace("中华人民共和国", "")
    parsed: list[ProvisionData] = []
    for number, text in provisions:
        node_key = f"{doc_key}#{number}"
        session.add(
            ProvisionNode(
                snapshot_id=snapshot.id,
                node_key=node_key,
                heading=f"第{number}条",
                text=text,
                text_hash=hashlib.sha256(text.encode()).hexdigest(),
            )
        )
        parsed.append(ProvisionData(node_key=node_key, heading=f"第{number}条", text=text))
    session.flush()

    register_document_hierarchy(session, doc, doc_key)
    promote_declared_authority(session, doc, doc_key, parsed)
    parent_key = derive_parent_key(doc_key)
    for prov in parsed:
        cites = extract_citations(prov.text, parent_key=parent_key, self_key=doc_key)
        if cites:
            store_citations(session, prov.node_key, cites)
    session.flush()
    return doc


@pytest.fixture
def consumption_tax(session):
    """消费税法 with an 实施条例 and a 公告 issued under Article 4."""
    statute = _ingest(
        session,
        "中华人民共和国消费税法",
        DocType.STATUTE,
        datetime(2024, 12, 25),
        [
            ("1", "在中华人民共和国境内销售应税消费品的单位和个人，为消费税的纳税人。"),
            ("4", "消费税的具体征收管理办法，由国务院税务主管部门规定。"),
        ],
    )
    _ingest(
        session,
        "中华人民共和国消费税法实施条例",
        DocType.REGULATION,
        datetime(2025, 6, 30),
        [("2", "税法第一条所称应税消费品，是指本条例所附税目税率表列举的消费品。")],
    )
    _ingest(
        session,
        "国家税务总局关于电池消费税征收管理有关事项的公告",
        DocType.ANNOUNCEMENT,
        datetime(2026, 7, 31),
        [
            (
                "1",
                "根据《中华人民共和国消费税法》第四条的规定，"
                "现将电池消费税征收管理有关事项公告如下。",
            ),
            ("2", "纳税人销售电池产品，应当选择电池类编码开具发票。"),
        ],
    )
    session.commit()
    return statute


# ---------------------------------------------------------------------------
# 公告 attaching to its statute
# ---------------------------------------------------------------------------


class TestAnnouncementAuthority:
    def test_announcement_becomes_a_child_of_the_statute(self, session, consumption_tax):
        """A 公告 names its parent in 第1條, not in its title."""
        children = {e.entity_key for e in get_family(session, "消费税法")["children"]}
        assert "国家税务总局关于电池消费税征收管理有关事项的公告" in children
        assert "消费税法实施条例" in children

    def test_authority_clause_naming_several_statutes_links_all(self, session):
        doc = _ingest(
            session,
            "税务人员税收业务违法行为处分规定",
            DocType.REGULATION,
            datetime(2026, 1, 1),
            [
                (
                    "1",
                    "根据《中华人民共和国公务员法》《中华人民共和国税收征收管理法》"
                    "的规定，制定本规定。",
                )
            ],
        )
        session.commit()

        assert doc.title in {
            e.entity_key for e in get_family(session, "税收征收管理法")["children"]
        }
        assert doc.title in {e.entity_key for e in get_family(session, "公务员法")["children"]}

    def test_authority_only_promoted_from_the_opening_provisions(self, session):
        """A 根据 deep in the body is argument, not a declaration of authority."""
        filler = [(str(i), "本条为无关内容。") for i in range(1, 5)]
        _ingest(
            session,
            "某某公告",
            DocType.ANNOUNCEMENT,
            datetime(2026, 1, 1),
            [*filler, ("9", "根据《中华人民共和国印花税法》的规定办理。")],
        )
        session.commit()

        assert get_family(session, "印花税法")["children"] == []


# ---------------------------------------------------------------------------
# Consolidated view
# ---------------------------------------------------------------------------


class TestConsolidatedView:
    def test_article_carries_its_implementing_provision(self, session, consumption_tax):
        view = svc.get_consolidated(session, "中华人民共和国消费税法")
        by_heading = {a["heading"]: a for a in view["articles"]}

        art1 = by_heading["第1条"]["supplements"]
        assert [s["node_key"] for s in art1] == ["消费税法实施条例#2"]
        assert "税目税率表" in art1[0]["text"]

        art4 = by_heading["第4条"]["supplements"]
        assert art4[0]["document_title"].startswith("国家税务总局关于电池")

    def test_supplement_shown_once_under_its_strongest_relation(self, session, consumption_tax):
        """A 依据 clause registers as both authority and interpretation."""
        view = svc.get_consolidated(session, "中华人民共和国消费税法")
        art4 = next(a for a in view["articles"] if a["heading"] == "第4条")
        assert len(art4["supplements"]) == 1
        assert art4["supplements"][0]["relation"] == "authority_of"

    def test_article_anchored_supplement_is_not_repeated_as_general(self, session, consumption_tax):
        """根据《消费税法》第四条 names both the law and the article."""
        view = svc.get_consolidated(session, "中华人民共和国消费税法")
        anchored = {s["node_key"] for a in view["articles"] for s in a["supplements"]}
        general = {s["node_key"] for s in view["unanchored_supplements"]}
        assert not (anchored & general)

    def test_statistics_count_articles_and_supplements(self, session, consumption_tax):
        stats = svc.get_consolidated(session, "中华人民共和国消费税法")["statistics"]
        assert stats["article_count"] == 2
        assert stats["supplemented_count"] == 2
        # 1条例 + 2公告 (公告#1 anchored, 公告#2 under promoted authority) + 1 unanchored
        assert stats["supplement_count"] == 4

    def test_lists_child_documents(self, session, consumption_tax):
        view = svc.get_consolidated(session, "中华人民共和国消费税法")
        titles = {c["key"] for c in view["child_documents"]}
        assert "消费税法实施条例" in titles

    def test_child_provisions_without_citation_appear_as_unanchored(self, session, consumption_tax):
        """公告#2 doesn't cite any parent article — it must still appear via child expansion."""
        view = svc.get_consolidated(session, "中华人民共和国消费税法")
        unanchored_keys = {s["node_key"] for s in view["unanchored_supplements"]}
        assert "国家税务总局关于电池消费税征收管理有关事项的公告#2" in unanchored_keys

    def test_child_own_cross_references_are_not_supplements(self, session):
        """本条例第三条 inside the regulation is internal, not another instrument."""
        _ingest(
            session,
            "中华人民共和国增值税法实施条例",
            DocType.REGULATION,
            datetime(2025, 1, 1),
            [
                ("3", "本条例所称销售额，是指全部价款。"),
                ("4", "本条例第三条所称价款，不包括代收款项。"),
            ],
        )
        session.commit()

        view = svc.get_consolidated(session, "中华人民共和国增值税法实施条例")
        assert all(not a["supplements"] for a in view["articles"])

    def test_calling_on_child_walks_up_to_parent(self, session, consumption_tax):
        """extract-requirements on the 实施条例 should still show the 母法."""
        view = svc.get_consolidated(session, "中华人民共和国消费税法实施条例")
        assert view["title"] == "中华人民共和国消费税法"
        assert view["statistics"]["article_count"] == 2

    def test_walk_up_stays_on_child_when_parent_has_no_provisions(self, session):
        """If the parent law isn't crawled, the child's own articles are used."""
        _ingest(
            session,
            "中华人民共和国增值税法实施条例",
            DocType.REGULATION,
            datetime(2025, 1, 1),
            [
                ("3", "本条例所称销售额，是指全部价款。"),
                ("4", "本条例第三条所称价款，不包括代收款项。"),
            ],
        )
        session.commit()

        view = svc.get_consolidated(session, "中华人民共和国增值税法实施条例")
        assert view["title"] == "中华人民共和国增值税法实施条例"
        assert view["statistics"]["article_count"] == 2
        # ...and the view says so, so nothing downstream mistakes the 实施条例
        # for the statute it implements.
        assert view["missing_parent"] == {"key": "增值税法", "status": "missing"}

    def test_view_rooted_at_a_statute_reports_no_missing_parent(self, session, consumption_tax):
        assert svc.get_consolidated(session, "中华人民共和国消费税法")["missing_parent"] is None

    def test_unknown_document(self, session):
        with pytest.raises(svc.DocumentNotFound):
            svc.get_consolidated(session, "no-such-law")


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


class TestIssueDates:
    def test_timeline_uses_the_issue_date_not_the_crawl_time(self, session, consumption_tax):
        history = documents_svc.get_history(session, "中华人民共和国消费税法")
        entry = history["timeline"][0]
        assert entry["date"].startswith("2024-12-25")
        assert entry["official_date"] is True
        assert entry["crawled_at"].startswith("2026-08-11")

    def test_versions_order_by_issue_date_not_crawl_order(self, session):
        """A back-filled older version must not sort as the newest."""
        doc = _ingest(
            session,
            "中华人民共和国印花税法",
            DocType.STATUTE,
            datetime(2021, 6, 10),
            [("1", "初版。")],
        )
        # Crawled later, but dated earlier — a correction fetched out of order.
        session.add(
            Snapshot(
                document_id=doc.id,
                content_hash="older",
                issued_at=datetime(2018, 1, 1),
                fetched_at=datetime(2026, 8, 12),
            )
        )
        session.commit()

        history = documents_svc.get_history(session, "中华人民共和国印花税法")
        dates = [t["date"][:10] for t in history["timeline"]]
        assert dates == ["2018-01-01", "2021-06-10"]

    def test_falls_back_to_crawl_time_and_says_so(self, session):
        _ingest(
            session,
            "無日期法規",
            DocType.STATUTE,
            None,
            [("1", "內容。")],
            fetched_at=datetime(2026, 8, 11),
        )
        session.commit()

        entry = documents_svc.get_history(session, "無日期法規")["timeline"][0]
        assert entry["date"].startswith("2026-08-11")
        assert entry["official_date"] is False

    def test_consolidated_view_reports_the_statute_version_date(self, session, consumption_tax):
        view = svc.get_consolidated(session, "中华人民共和国消费税法")
        assert view["as_of"].startswith("2024-12-25")
        assert view["official_date"] is True

    def test_document_level_authority_expands_all_provisions(self, session, consumption_tax):
        """When an announcement declares authority over a statute, all its articles expand."""
        view = svc.get_consolidated(session, "中华人民共和国消费税法")
        art4 = next(a for a in view["articles"] if a["heading"] == "第4条")
        supp_nodes = [s["node_key"] for s in art4["supplements"]]
        unanchored_nodes = [s["node_key"] for s in view["unanchored_supplements"]]
        # Article 1 cites Article 4; Article 2 expands via promoted document authority / child supplements
        assert "国家税务总局关于电池消费税征收管理有关事項的公告#1" in supp_nodes or "国家税务总局关于电池消费税征收管理有关事项的公告#1" in supp_nodes
        assert "国家税务总局关于电池消费税征收管理有关事項的公告#2" in unanchored_nodes or "国家税务总局关于电池消费税征收管理有关事项的公告#2" in unanchored_nodes
