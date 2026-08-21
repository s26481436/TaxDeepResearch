"""Whatever the document path reports, the tax path must report too.

This has now been missed twice. `preview` was aggregated but not printed
(#47), and the dimension warnings were populated but neither aggregated nor
printed — so `tw_income --dry-run` showed 34 rows, no identity_key and no
explanation, which reads exactly like a clean run.
"""

from __future__ import annotations

import hashlib
import inspect
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

import taxwatch.cli as cli
import taxwatch.requirements.extract as ext
from taxwatch.models import DocType, Document, ProvisionNode, Snapshot, Source
from taxwatch.requirements.schema import RequirementOut, RequirementSetOut


@pytest.fixture
def tw_law(session):
    source = Source(key="tw-moj-law", country="TW", connector="tw_moj_law")
    session.add(source)
    session.flush()
    doc = Document(
        source_id=source.id,
        external_id="tw-income",
        doc_type=DocType.STATUTE,
        title="所得稅法",
    )
    session.add(doc)
    session.flush()
    snap = Snapshot(document_id=doc.id, content_hash="v1", issued_at=datetime(2025, 1, 1))
    session.add(snap)
    session.flush()
    text = "凡有中華民國來源所得之個人，應課徵綜合所得稅。"
    session.add(
        ProvisionNode(
            snapshot_id=snap.id,
            node_key="所得稅法#2",
            heading="第2條",
            text=text,
            text_hash=hashlib.sha256(text.encode()).hexdigest(),
        )
    )
    session.commit()
    return doc


def test_tax_path_aggregates_the_dimension_stats(session, tw_law):
    client = MagicMock(model="m")
    client.generate_structured.return_value = RequirementSetOut(
        requirements=[RequirementOut(scenario="居住者個人", taxpayer_role="個人")],
        unresolved=[],
    )

    with patch("taxwatch.requirements.extract.get_llm_client", return_value=client):
        stats = ext.extract_for_tax(session, "tw_income", dry_run=True)

    for key in ("rows_without_identity", "unknown_dimension_values", "incomplete_dimensions"):
        assert key in stats, f"tax path dropped {key}"
    assert stats["rows_without_identity"] == 1


def test_both_cli_paths_call_the_same_reporter():
    """One shared function, invoked from both — not two copies that drift."""
    source = inspect.getsource(cli.extract_requirements)
    assert source.count("_echo_identity_report(") == 2


def test_reporter_confirms_the_healthy_case(capsys):
    """Silence cannot distinguish "all rows have an identity" from "never ran"."""
    cli._echo_identity_report(
        {
            "rows_without_identity": 0,
            "requirements_emitted": 29,
            "unknown_dimension_values": [],
            "incomplete_dimensions": [],
        }
    )
    out = capsys.readouterr().out
    assert "29/29" in out
    assert "✓" in out


def test_reporter_stays_quiet_when_there_are_no_rows(capsys):
    cli._echo_identity_report({"rows_without_identity": 0, "requirements_emitted": 0})
    assert capsys.readouterr().out == ""


def test_tax_path_aggregates_requirements_emitted(session, tw_law):
    client = MagicMock(model="m")
    client.generate_structured.return_value = RequirementSetOut(
        requirements=[RequirementOut(scenario="居住者個人", taxpayer_role="個人")],
        unresolved=[],
    )
    with patch("taxwatch.requirements.extract.get_llm_client", return_value=client):
        stats = ext.extract_for_tax(session, "tw_income", dry_run=True)
    assert stats["requirements_emitted"] == 1


def test_both_cli_paths_call_the_same_preview_printer():
    source = inspect.getsource(cli.extract_requirements)
    assert source.count("_echo_preview(") == 2


def test_preview_shows_the_identity_key(capsys):
    cli._echo_preview(
        {
            "preview": [
                {
                    "identity_key": "resident_individual|annual_filing|general_income|standard",
                    "scenario": "居住者綜合所得稅結算申報",
                    "taxpayer_role": "個人",
                }
            ]
        }
    )
    out = capsys.readouterr().out
    assert "resident_individual|annual_filing|general_income|standard" in out
    assert "居住者綜合所得稅結算申報" in out


def test_preview_falls_back_to_text_without_an_identity(capsys):
    cli._echo_preview(
        {"preview": [{"identity_key": "", "scenario": "某情境", "taxpayer_role": ""}]}
    )
    out = capsys.readouterr().out
    assert "某情境" in out
    assert "（未分身分）" in out


def test_reporter_names_the_consequence(capsys):
    cli._echo_identity_report(
        {"rows_without_identity": 34, "requirements_emitted": 34}
    )
    out = capsys.readouterr().out
    assert "34/34" in out
    assert "不會被更新" in out, "the consequence is what makes this actionable"


def test_long_lists_are_truncated(capsys):
    cli._echo_identity_report(
        {
            "rows_without_identity": 0,
            "unknown_dimension_values": [f"dim{i}：bogus" for i in range(30)],
        }
    )
    out = capsys.readouterr().out
    assert "另有 10 筆" in out
