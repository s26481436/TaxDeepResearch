"""--dry-run must say why a row has no identity_key.

A real run printed 34 scenarios, none of them carrying an identity_key and no
warning explaining it — because the dimension warnings were only appended in
the upsert loop, which `--dry-run` returns before reaching. The verification
path showed neither the key nor a reason for its absence.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

import taxwatch.requirements.extract as ext
from taxwatch.models import DocType, Document, ProvisionNode, Snapshot, Source, TaxRequirement
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
    text = "凡有中華民國來源所得之個人，應就其中華民國來源之所得，課徵綜合所得稅。"
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


def _run(session, row, *, dry_run=True):
    client = MagicMock(model="m")
    client.generate_structured.return_value = RequirementSetOut(
        requirements=[row], unresolved=[]
    )
    with patch("taxwatch.requirements.extract.get_llm_client", return_value=client):
        return ext.extract_for_document(
            session, "tw-income", country="TW", tax_key="tw_income", dry_run=dry_run
        )


def test_a_row_with_no_dimensions_at_all_is_counted(session, tw_law):
    """Reported in aggregate, not per dimension.

    A run where the model ignored the vocabulary entirely would otherwise emit
    four warning lines per row — 136 lines for the 34-row run that prompted
    this. The count is the signal; the per-dimension detail is reserved for
    partial rows, where it points at a vocabulary that may be short a value.
    """
    row = RequirementOut(scenario="居住者個人綜合所得稅", taxpayer_role="個人")
    stats = _run(session, row)

    assert stats["rows_without_identity"] == 1
    assert stats["incomplete_dimensions"] == []


def test_a_partially_filled_row_names_the_absent_dimension(session, tw_law):
    """Here the detail is actionable: three of four were derivable, one was not."""
    row = RequirementOut(
        scenario="居住者個人綜合所得稅",
        taxpayer_role="個人",
        taxpayer_class="resident_individual",
        tax_scheme="annual_filing",
        subject_matter="general_income",
    )
    stats = _run(session, row)

    assert stats["rows_without_identity"] == 1
    assert any("scenario_key" in item for item in stats["incomplete_dimensions"])


def test_unknown_dimension_values_are_reported_in_dry_run(session, tw_law):
    row = RequirementOut(
        scenario="居住者個人綜合所得稅",
        taxpayer_role="個人",
        taxpayer_class="not_a_real_class",
        tax_scheme="annual_filing",
        subject_matter="general_income",
        scenario_key="standard",
    )
    stats = _run(session, row)

    assert stats["rows_without_identity"] == 1
    assert any("not_a_real_class" in item for item in stats["unknown_dimension_values"])


def test_a_complete_row_reports_no_problem(session, tw_law):
    row = RequirementOut(
        scenario="居住者個人綜合所得稅",
        taxpayer_role="個人",
        taxpayer_class="resident_individual",
        tax_scheme="annual_filing",
        subject_matter="general_income",
        scenario_key="standard",
    )
    stats = _run(session, row)

    assert stats["rows_without_identity"] == 0
    assert stats["unknown_dimension_values"] == []
    assert stats["incomplete_dimensions"] == []
    assert stats["preview"][0]["identity_key"] == (
        "resident_individual|annual_filing|general_income|standard"
    )


def test_reports_are_not_duplicated_on_a_real_write(session, tw_law):
    """The warnings moved ahead of the dry-run exit; they must still fire once."""
    row = RequirementOut(scenario="居住者個人綜合所得稅", taxpayer_role="個人")
    stats = _run(session, row, dry_run=False)

    assert session.query(TaxRequirement).count() == 1
    assert stats["rows_without_identity"] == 1
