"""Tests for document version-history queries."""

from datetime import date

import pytest

from taxwatch.services import documents as svc


def test_history_lists_versions_oldest_first(session, seeded):
    history = svc.get_history(session, "cn-enterprise-income-tax-law")

    assert history["version_count"] == 3
    assert [v["version"] for v in history["timeline"]] == ["v1", "v2", "v3"]
    assert history["first_seen"].startswith("2020-06-01")
    assert history["last_updated"].startswith("2026-08-01")
    assert history["tax_key"] == "enterprise_income"


def test_history_attaches_changes_to_the_version_they_produced(session, seeded):
    timeline = svc.get_history(session, "cn-enterprise-income-tax-law")["timeline"]

    assert timeline[0]["changes"] == []  # v1 is the first capture
    assert timeline[1]["changes"][0]["node_key"] == "企业所得税法#28"
    assert timeline[2]["changes"][0]["severity"] == "critical"


def test_history_counts_provisions_per_version(session, seeded):
    timeline = svc.get_history(session, "cn-enterprise-income-tax-law")["timeline"]
    assert [v["provision_count"] for v in timeline] == [2, 2, 3]


def test_history_resolves_by_title_too(session, seeded):
    history = svc.get_history(session, "中华人民共和国企业所得税法")
    assert history["external_id"] == "cn-enterprise-income-tax-law"


def test_history_unknown_document(session, seeded):
    with pytest.raises(svc.DocumentNotFound):
        svc.get_history(session, "no-such-law")


def test_version_at_returns_the_version_in_force(session, seeded):
    """A date between two snapshots must resolve to the earlier one."""
    version = svc.get_version_at(session, "cn-enterprise-income-tax-law", date(2024, 1, 1))

    assert version["snapshot_date"].startswith("2023-06-01")
    art28 = next(p for p in version["provisions"] if p["node_key"] == "企业所得税法#28")
    assert "15%" in art28["text"]


def test_version_at_latest_date(session, seeded):
    version = svc.get_version_at(session, "cn-enterprise-income-tax-law", date(2026, 12, 31))
    assert version["provision_count"] == 3
    art28 = next(p for p in version["provisions"] if p["node_key"] == "企业所得税法#28")
    assert "25%" in art28["text"]


def test_version_at_before_first_snapshot(session, seeded):
    with pytest.raises(svc.SnapshotNotFound):
        svc.get_version_at(session, "cn-enterprise-income-tax-law", date(2019, 1, 1))


def test_diff_across_six_years(session, seeded):
    """The 2020 → 2026 question: one modified article, one added."""
    diff = svc.get_diff(
        session, "cn-enterprise-income-tax-law", date(2020, 6, 1), date(2026, 12, 31)
    )

    assert diff["summary"]["modified"] == 1
    assert diff["summary"]["added"] == 1
    assert diff["summary"]["removed"] == 0

    modified = next(d for d in diff["diffs"] if d["change_type"] == "modified")
    assert modified["node_key"] == "企业所得税法#28"
    assert "20%" in modified["old_text"]
    assert "25%" in modified["new_text"]

    added = next(d for d in diff["diffs"] if d["change_type"] == "added")
    assert added["node_key"] == "企业所得税法#43"


def test_diff_skips_the_intermediate_version(session, seeded):
    """A 2020→2026 diff compares endpoints, so the 15% interim never appears."""
    diff = svc.get_diff(
        session, "cn-enterprise-income-tax-law", date(2020, 6, 1), date(2026, 12, 31)
    )
    modified = next(d for d in diff["diffs"] if d["node_key"] == "企业所得税法#28")
    assert "15%" not in modified["old_text"]
    assert "15%" not in modified["new_text"]


def test_diff_within_one_version_is_flagged_unchanged(session, seeded):
    diff = svc.get_diff(session, "cn-enterprise-income-tax-law", date(2020, 7, 1), date(2021, 1, 1))
    assert diff["unchanged"] is True
    assert diff["summary"]["total"] == 0


def test_list_documents_reports_version_count(session, seeded):
    rows = svc.list_documents(session)
    assert len(rows) == 1
    assert rows[0]["version_count"] == 3
    assert rows[0]["tax_name"] == "企業所得稅"
    assert rows[0]["country"] == "CN"


def test_list_documents_filters(session, seeded):
    assert svc.list_documents(session, country="CN")
    assert svc.list_documents(session, country="TW") == []
    assert svc.list_documents(session, tax_key="enterprise_income")
    assert svc.list_documents(session, tax_key="vat") == []


class TestFindDocumentByFragment:
    """The crawler mints opaque ids (c5251620, a 文號); nobody types those.

    Accepting a distinctive fragment of the title is what makes the CLI usable,
    and an ambiguous fragment must report the candidates rather than silently
    picking whichever row came back first.
    """

    @pytest.fixture
    def cn_docs(self, session):
        from taxwatch.models import DocType, Source
        from taxwatch.models import Document as Doc

        source = Source(key="cn-chinatax", country="CN", connector="cn_chinatax")
        session.add(source)
        session.flush()
        for ext, title, dt in [
            ("c5251620", "中华人民共和国增值税法", DocType.STATUTE),
            ("c5251406", "中华人民共和国增值税法实施条例", DocType.REGULATION),
            ("c5250999", "中华人民共和国消费税法", DocType.STATUTE),
        ]:
            session.add(Doc(source_id=source.id, external_id=ext, doc_type=dt, title=title))
        session.commit()
        return session

    def test_exact_external_id(self, cn_docs):
        assert svc.find_document(cn_docs, "c5251620").title == "中华人民共和国增值税法"

    def test_exact_title(self, cn_docs):
        assert svc.find_document(cn_docs, "中华人民共和国消费税法").external_id == "c5250999"

    def test_unique_fragment(self, cn_docs):
        assert svc.find_document(cn_docs, "消费税法").external_id == "c5250999"

    def test_ambiguous_fragment_reports_candidates(self, cn_docs):
        from taxwatch.models import DocType, Source
        from taxwatch.models import Document as Doc

        source = cn_docs.query(Source).filter_by(key="cn-chinatax").one()
        cn_docs.add(
            Doc(
                source_id=source.id,
                external_id="c5250888",
                doc_type=DocType.STATUTE,
                title="中华人民共和国企业所得税法",
            )
        )
        cn_docs.add(
            Doc(
                source_id=source.id,
                external_id="c5250889",
                doc_type=DocType.STATUTE,
                title="中华人民共和国个人所得税法",
            )
        )
        cn_docs.commit()

        with pytest.raises(svc.AmbiguousDocument) as caught:
            svc.find_document(cn_docs, "所得税法")
        assert caught.value.candidates == [
            "中华人民共和国个人所得税法",
            "中华人民共和国企业所得税法",
        ]

    def test_working_name_resolves_to_the_statute_not_its_regulation(self, cn_docs):
        """增值税法 is the law's working name — and a literal prefix of its own
        实施条例. The statute is what was asked for."""
        assert svc.find_document(cn_docs, "增值税法").external_id == "c5251620"

    def test_exact_title_wins_over_being_a_prefix_of_another(self, cn_docs):
        """增值税法 is a substring of its own 实施条例, so an exact title must
        not be treated as ambiguous."""
        doc = svc.find_document(cn_docs, "中华人民共和国增值税法")
        assert doc.external_id == "c5251620"

    def test_unknown_still_raises(self, cn_docs):
        with pytest.raises(svc.DocumentNotFound):
            svc.find_document(cn_docs, "cn-vat-law")


class TestParentLawMissing:
    """The 子法 must never stand in for the 母法 nobody fetched.

    《…增值税法实施条例》 contains 《…增值税法》 as a prefix, so with the statute
    absent the regulation is the sole substring match — and used to be returned
    as though someone had asked for it.
    """

    @pytest.fixture
    def child_only(self, session):
        from taxwatch.models import DocType, Source
        from taxwatch.models import Document as Doc

        source = Source(key="cn-chinatax", country="CN", connector="cn_chinatax")
        session.add(source)
        session.flush()
        session.add(
            Doc(
                source_id=source.id,
                external_id="c5251406",
                doc_type=DocType.REGULATION,
                title="中华人民共和国增值税法实施条例",
            )
        )
        session.commit()
        return session

    def test_asking_for_the_statute_names_the_children_found_instead(self, child_only):
        with pytest.raises(svc.ParentLawMissing) as caught:
            svc.find_document(child_only, "中华人民共和国增值税法")
        assert caught.value.children == ["中华人民共和国增值税法实施条例"]

    def test_working_name_of_the_statute_raises_too(self, child_only):
        with pytest.raises(svc.ParentLawMissing):
            svc.find_document(child_only, "增值税法")

    def test_the_child_itself_still_resolves(self, child_only):
        doc = svc.find_document(child_only, "增值税法实施条例")
        assert doc.external_id == "c5251406"


class TestSuggestDocuments:
    def test_falls_back_to_listing_everything_when_nothing_matches(self, session):
        from taxwatch.models import DocType, Source
        from taxwatch.models import Document as Doc

        source = Source(key="cn-chinatax", country="CN", connector="cn_chinatax")
        session.add(source)
        session.flush()
        session.add(
            Doc(
                source_id=source.id,
                external_id="c1",
                doc_type=DocType.STATUTE,
                title="中华人民共和国增值税法",
            )
        )
        session.commit()

        # "cn-vat-law" matches nothing, but the caller still needs something
        # actionable to show.
        rows = svc.suggest_documents(session, "cn-vat-law")
        assert [r["external_id"] for r in rows] == ["c1"]

    def test_empty_database_suggests_nothing(self, session):
        assert svc.suggest_documents(session, "anything") == []
