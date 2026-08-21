"""The matrix must not present unchecked rows as checked.

Coverage varies between extractions — 虧損扣除 appears in one pass and OBU in
the next. Because upsert matches on identity_key, a row the latest pass missed
is neither duplicated nor deleted: it stays, unrefreshed, and looked exactly
like a row that had just been confirmed against current law.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from taxwatch.models import TaxRequirement
from taxwatch.services.requirements import _mark_unconfirmed, list_requirements

OLD = datetime(2026, 1, 1)
NEW = datetime(2026, 8, 1)


def _add(session, scenario, seen, tax_key="tw_income", country="TW"):
    req = TaxRequirement(
        country=country, tax_key=tax_key, scenario=scenario, last_seen_at=seen
    )
    session.add(req)
    return req


def test_a_row_the_latest_pass_missed_is_flagged(session):
    _add(session, "confirmed", NEW)
    _add(session, "missed", OLD)
    session.commit()

    rows = {r["scenario"]: r for r in list_requirements(session)}
    assert rows["missed"]["unconfirmed"] is True
    assert rows["confirmed"]["unconfirmed"] is False


def test_every_row_of_one_pass_shares_its_timestamp(session):
    """Otherwise "latest pass" degrades into guessing how long a run takes."""
    _add(session, "a", NEW)
    _add(session, "b", NEW)
    session.commit()

    assert not any(r["unconfirmed"] for r in list_requirements(session))


def test_tax_types_are_judged_separately(session):
    """An 所得稅 run says nothing about whether 增值稅 rows are current."""
    _add(session, "income", NEW, tax_key="tw_income")
    _add(session, "vat", OLD, tax_key="cn_vat", country="CN")
    session.commit()

    rows = {r["scenario"]: r for r in list_requirements(session)}
    assert rows["vat"]["unconfirmed"] is False


def test_a_never_stamped_tax_type_is_not_called_stale(session):
    """Rows predating the column have no timestamp; that is not evidence."""
    _add(session, "legacy", None)
    session.commit()

    assert list_requirements(session)[0]["unconfirmed"] is False


def test_a_row_without_a_stamp_among_stamped_rows_is_flagged():
    rows = [
        {"country": "TW", "tax_key": "tw_income", "last_seen_at": NEW.isoformat()},
        {"country": "TW", "tax_key": "tw_income", "last_seen_at": None},
    ]
    _mark_unconfirmed(rows)
    assert rows[1]["unconfirmed"] is True


def test_extraction_stamps_every_row_it_produces(session):
    """A run that writes rows without stamping them makes them all look stale."""
    from unittest.mock import MagicMock, patch

    import taxwatch.requirements.extract as ext

    before = datetime.utcnow() - timedelta(seconds=1)
    req = _add(session, "s", OLD)
    session.commit()

    ext._upsert_requirement(
        session,
        MagicMock(scenario="s", taxpayer_role="", fields=[], unresolved=[]),
        country="TW",
        tax_key="tw_income",
        document=None,
        allowed_nodes=set(),
        model="m",
    )
    session.commit()
    assert req.last_seen_at > before
