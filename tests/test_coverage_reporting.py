"""A topic missing from one run is survivable, but must not be silent.

Two runs of 所得稅法 at req-v10 covered 虧損扣除 and 變更會計年度 in one,
OBU and 受益人不特定 in the next. Because upsert matches on identity_key, the
missed rows are not duplicated or deleted — they simply are not refreshed. The
row count is identical either way, so nothing in the output said which topic
went unchecked.
"""

from __future__ import annotations

import inspect

import taxwatch.cli as cli
from taxwatch.requirements.extract import _intersect_unused, _unused_dimension_values


def _rows(*dim_sets):
    return [{"valid_dims": dims} for dims in dim_sets]


def test_names_the_values_no_row_used():
    unused = _unused_dimension_values(
        "TW",
        "tw_income",
        _rows({"taxpayer_class": "all_taxpayers", "scenario_key": "standard"}),
    )
    assert any("loss_carryforward" in u for u in unused)
    assert not any("scenario_key=standard" in u for u in unused)


def test_a_used_value_is_not_reported_missing():
    unused = _unused_dimension_values(
        "TW", "tw_income", _rows({"scenario_key": "offshore_banking_unit"})
    )
    assert not any("offshore_banking_unit" in u for u in unused)


def test_an_undefined_vocabulary_reports_nothing():
    assert _unused_dimension_values("XX", "xx_undefined", _rows({})) == []


def test_documents_intersect_rather_than_accumulate():
    """One statute cannot express every value; the matrix as a whole might."""
    assert _intersect_unused(None, ["a", "b"]) == ["a", "b"]
    assert _intersect_unused(["a", "b"], ["b", "c"]) == ["b"]
    assert _intersect_unused(["a"], []) == []


def test_report_states_that_the_rows_survive_unrefreshed(capsys):
    cli._echo_coverage_report(
        {
            "unused_dimension_values": ["scenario_key=loss_carryforward"],
            "dimension_values_total": 25,
        }
    )
    out = capsys.readouterr().out
    assert "loss_carryforward" in out
    assert "未重新確認" in out, "the consequence is the point, not the list"


def test_full_coverage_is_stated_rather_than_implied(capsys):
    """Silence would mean both "all covered" and "no vocabulary at all"."""
    cli._echo_coverage_report({"unused_dimension_values": [], "dimension_values_total": 25})
    out = capsys.readouterr().out
    assert "25/25" in out
    assert "✓" in out


def test_silent_when_the_tax_regime_has_no_vocabulary(capsys):
    cli._echo_coverage_report({"unused_dimension_values": [], "dimension_values_total": 0})
    assert capsys.readouterr().out == ""


def test_both_cli_paths_report_coverage():
    source = inspect.getsource(cli.extract_requirements)
    assert source.count("_echo_coverage_report(") == 2


def test_a_confirmed_row_records_when_it_was_last_seen(session):
    """`updated_at` cannot answer this: onupdate fires only on a real change."""
    from taxwatch.models import TaxRequirement

    req = TaxRequirement(country="TW", tax_key="tw_income", scenario="s")
    session.add(req)
    session.commit()
    assert req.last_seen_at is None, "never extracted is not the same as just confirmed"
