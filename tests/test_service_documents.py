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
    diff = svc.get_diff(
        session, "cn-enterprise-income-tax-law", date(2020, 7, 1), date(2021, 1, 1)
    )
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
