"""Tests for tax-type rollups and dashboard aggregates."""
import pytest

from taxwatch.services import dashboard as dash_svc
from taxwatch.services import tax_types as svc


def test_list_tax_types_groups_by_taxonomy(session, seeded):
    rows = svc.list_tax_types(session, recent_days=7)
    assert len(rows) == 1

    row = rows[0]
    assert row["key"] == "enterprise_income"
    assert row["name"] == "企業所得稅"
    assert row["countries"] == ["CN"]
    assert row["document_count"] == 1
    assert row["version_count"] == 3


def test_list_tax_types_counts_only_changes_in_window(session, seeded):
    """The 2023 change falls outside a 7-day window; the recent one does not."""
    recent = svc.list_tax_types(session, recent_days=7)[0]
    assert recent["recent_changes"] == 1
    assert recent["critical_changes"] == 1
    assert recent["status"] == "critical"

    wide = svc.list_tax_types(session, recent_days=3650)[0]
    assert wide["recent_changes"] == 2


def test_list_tax_types_reports_freshness(session, seeded):
    row = svc.list_tax_types(session)[0]
    assert row["last_updated"].startswith("2026-08-01")
    assert row["days_since_update"] is not None


def test_summary_includes_documents_and_analysed_changes(session, seeded):
    summary = svc.get_summary(session, "enterprise_income", recent_days=3650)

    assert summary["statistics"]["document_count"] == 1
    assert summary["statistics"]["version_count"] == 3
    assert summary["statistics"]["change_count"] == 2
    assert summary["statistics"]["analysed_count"] == 1
    assert summary["statistics"]["average_confidence"] == 0.9

    analysed = next(c for c in summary["changes"] if c["confidence"] is not None)
    assert analysed["effective_date"] == "2026-01-01"
    assert "制造业" in analysed["affected_parties"]


def test_summary_documents_carry_current_provision_count(session, seeded):
    summary = svc.get_summary(session, "enterprise_income", recent_days=3650)
    assert summary["documents"][0]["provision_count"] == 3


def test_summary_changes_are_newest_first(session, seeded):
    changes = svc.get_summary(session, "enterprise_income", recent_days=3650)["changes"]
    assert changes[0]["detected_at"] > changes[1]["detected_at"]


def test_summary_unknown_tax_type(session, seeded):
    with pytest.raises(svc.TaxTypeNotFound):
        svc.get_summary(session, "vat")


def test_dashboard_stats(session, seeded):
    stats = dash_svc.get_stats(session, recent_days=7)
    assert stats["tax_type_count"] == 1
    assert stats["document_count"] == 1
    assert stats["snapshot_count"] == 3
    assert stats["recent_changes"] == 1
    assert stats["pending_review"] == 0
    assert stats["average_confidence"] == 0.9


def test_dashboard_stats_counts_unanalysed_as_pending(session, seeded):
    """The 2023 change has no Analysis row, so a wide window shows it as pending."""
    stats = dash_svc.get_stats(session, recent_days=3650)
    assert stats["recent_changes"] == 2
    assert stats["pending_review"] == 1


def test_list_changes_filters(session, seeded):
    assert len(dash_svc.list_changes(session, days=3650)) == 2
    assert len(dash_svc.list_changes(session, days=3650, severity="critical")) == 1
    assert dash_svc.list_changes(session, days=3650, country="TW") == []
    assert dash_svc.list_changes(session, days=3650, tax_key="vat") == []


def test_list_changes_respects_limit(session, seeded):
    assert len(dash_svc.list_changes(session, days=3650, limit=1)) == 1


def test_change_detail_pairs_old_and_new_text(session, seeded):
    change_id = seeded["changes"][1].id
    detail = dash_svc.get_change_detail(session, change_id)

    assert "15%" in detail["old_text"]
    assert "25%" in detail["new_text"]
    assert detail["analysis"]["confidence"] == 0.9
    assert detail["analysis"]["citations"][0]["article"] == "28"


def test_change_detail_without_analysis(session, seeded):
    detail = dash_svc.get_change_detail(session, seeded["changes"][0].id)
    assert detail["analysis"] is None


def test_change_detail_missing(session, seeded):
    with pytest.raises(dash_svc.ChangeNotFound):
        dash_svc.get_change_detail(session, 99999)


def test_run_health(session, seeded):
    health = dash_svc.get_run_health(session)
    assert health["total"] == 1
    assert health["success_rate"] == 1.0
    assert health["failed"] == 0


def test_run_health_empty(session):
    assert dash_svc.get_run_health(session)["total"] == 0
