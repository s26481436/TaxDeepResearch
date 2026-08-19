"""Purging requirement rows must be reversible.

Regenerating a matrix with different scenario or role wording leaves the old
rows behind — upsert keys on those very fields. Clearing them is routine, so
losing hand-reviewed content to it must not be.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from taxwatch.models import FieldSource, RequirementField, TaxRequirement

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "purge_requirements.py"


@pytest.fixture
def rows(session):
    for tax_key, scenario in (("tw_other", "技術服務費"), ("tw_profit_seeking", "未分配盈餘")):
        req = TaxRequirement(
            country="TW",
            tax_key=tax_key,
            scenario=scenario,
            taxpayer_role="納稅義務人",
            notes="gpt-researcher",
        )
        session.add(req)
        session.flush()
        session.add(
            RequirementField(
                requirement_id=req.id,
                field_key="rate",
                value="20%",
                source=FieldSource.IMPORT,
                needs_review=True,
                review_reason="待確認",
            )
        )
    session.commit()
    return session


def test_script_is_syntactically_importable():
    """The script is run by hand on a server; a syntax error must not wait."""
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(SCRIPT)], capture_output=True
    )
    assert result.returncode == 0, result.stderr.decode()


def test_dump_round_trips_every_field(session, rows):
    import importlib.util

    spec = importlib.util.spec_from_file_location("purge", SCRIPT)
    purge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(purge)

    payload = purge._dump(session.query(TaxRequirement).all())

    assert len(payload) == 2
    first = payload[0]
    assert {"country", "tax_key", "scenario", "taxpayer_role", "notes", "fields"} <= set(first)
    assert first["fields"][0]["value"] == "20%"
    assert first["fields"][0]["review_reason"] == "待確認"
    # Must survive a JSON round trip — it is written to disk as the only copy.
    assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload


def test_deleting_a_requirement_takes_its_fields(session, rows):
    req = session.query(TaxRequirement).first()
    req_id = req.id
    session.delete(req)
    session.commit()

    assert session.query(RequirementField).filter_by(requirement_id=req_id).count() == 0
