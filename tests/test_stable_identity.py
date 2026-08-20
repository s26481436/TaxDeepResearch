from datetime import datetime
from unittest.mock import MagicMock

import pytest

from taxwatch.models import (
    DocType,
    Document,
    ProvisionNode,
    Snapshot,
    Source,
)
from taxwatch.requirements.dimensions import (
    compute_identity_key,
    get_dimensions_vocabulary,
    validate_dimensions,
)


@pytest.fixture
def tw_law(session):
    source = Source(key="tw-mof", country="TW", connector="tw_mof")
    session.add(source)
    session.flush()

    doc = Document(
        source_id=source.id,
        external_id="tw-income-tax-act",
        doc_type=DocType.STATUTE,
        title="中華民國所得稅法",
        issued_at=datetime(2024, 12, 25),
    )
    session.add(doc)
    session.flush()

    snap = Snapshot(
        document_id=doc.id,
        content_hash="h1",
        issued_at=datetime(2024, 12, 25),
        fetched_at=datetime(2026, 8, 1),
    )
    session.add(snap)
    session.flush()

    p2 = ProvisionNode(
        snapshot_id=snap.id,
        node_key="所得稅法#2",
        heading="第二條",
        text="所得稅之納稅義務人...",
        text_hash="h2",
    )
    session.add(p2)
    session.commit()
    return doc


def test_vocabulary_per_tax_regime():
    """1. 詞彙表以 (country, tax_key) 為單位，未定義的稅種回傳空."""
    vocab_tw = get_dimensions_vocabulary("TW", "tw_income")
    assert "taxpayer_class" in vocab_tw
    assert "tax_scheme" in vocab_tw
    assert "subject_matter" in vocab_tw
    assert "scenario_key" in vocab_tw

    vocab_cn = get_dimensions_vocabulary("CN", "cn_vat")
    assert vocab_cn == {}

    vocab_unknown = get_dimensions_vocabulary("US", "us_cit")
    assert vocab_unknown == {}


def test_identity_key_fixed_order():
    """2. identity_key 由四維度依固定順序組成，順序不因輸入順序改變."""
    dims1 = {
        "taxpayer_class": "resident_individual",
        "tax_scheme": "annual_filing",
        "subject_matter": "general_income",
        "scenario_key": "standard",
    }
    dims2 = {
        "scenario_key": "standard",
        "subject_matter": "general_income",
        "taxpayer_class": "resident_individual",
        "tax_scheme": "annual_filing",
    }
    expected = "resident_individual|annual_filing|general_income|standard"
    assert compute_identity_key(dims1) == expected
    assert compute_identity_key(dims2) == expected


def test_identity_key_empty_when_all_empty():
    """3. 四維度皆空時 identity_key 為空."""
    assert compute_identity_key({}) == ""
    assert compute_identity_key(None) == ""
    assert (
        compute_identity_key(
            {
                "taxpayer_class": "",
                "tax_scheme": "",
                "subject_matter": "",
                "scenario_key": "",
            }
        )
        == ""
    )


@pytest.mark.parametrize(
    "missing_field",
    ["taxpayer_class", "tax_scheme", "subject_matter", "scenario_key"],
)
def test_identity_key_empty_when_any_dimension_missing(missing_field):
    """任一維度為空 → identity_key 為空（防止部分身分碰撞）."""
    full = {
        "taxpayer_class": "resident_individual",
        "tax_scheme": "annual_filing",
        "subject_matter": "general_income",
        "scenario_key": "standard",
    }
    full[missing_field] = ""
    assert compute_identity_key(full) == ""


def test_validate_dimensions_distinguishes_missing_and_invalid():
    """驗證 validate_dimensions 可區分「值不合法」與「值缺漏」兩種情況."""
    raw = {
        "taxpayer_class": "resident_individual",  # Valid
        "tax_scheme": "invalid_scheme",           # Invalid
        "subject_matter": "",                     # Missing
        "scenario_key": "standard",               # Valid
    }
    valid_dims, unknowns, missing = validate_dimensions("TW", "tw_income", raw)

    assert valid_dims["taxpayer_class"] == "resident_individual"
    assert valid_dims["scenario_key"] == "standard"
    assert valid_dims["tax_scheme"] == ""
    assert valid_dims["subject_matter"] == ""

    assert unknowns == [("tax_scheme", "invalid_scheme")]
    assert missing == ["subject_matter"]
    # identity_key must be empty due to incomplete valid dimensions
    assert compute_identity_key(valid_dims) == ""


def test_upsert_same_identity_key_updates_description(session):
    """4. 相同 identity_key、不同 scenario 文字 → 更新同一列，且描述被更新."""
    from taxwatch.models import TaxRequirement
    from taxwatch.requirements.extract import _upsert_requirement
    from taxwatch.requirements.schema import RequirementFieldOut, RequirementOut

    row1 = RequirementOut(
        scenario="綜合所得稅：中華民國境內居住之個人",
        taxpayer_role="一般納稅人（居住者）",
        taxpayer_class="resident_individual",
        tax_scheme="annual_filing",
        subject_matter="general_income",
        scenario_key="standard",
        fields=[
            RequirementFieldOut(
                field_key="rate",
                value="5% - 40%",
                confidence=1.0,
            )
        ],
    )
    _upsert_requirement(
        session,
        row1,
        country="TW",
        tax_key="tw_income",
        document=None,
        allowed_nodes=set(),
        model="test-model",
    )
    session.commit()

    reqs1 = session.query(TaxRequirement).all()
    assert len(reqs1) == 1
    req1 = reqs1[0]
    assert req1.identity_key == "resident_individual|annual_filing|general_income|standard"
    assert req1.scenario == "綜合所得稅：中華民國境內居住之個人"

    # Second run with different human description but identical dimensions
    row2 = RequirementOut(
        scenario="個人（中華民國境內居住者）取得中華民國來源所得",
        taxpayer_role="個人 - 綜合所得稅納稅義務人",
        taxpayer_class="resident_individual",
        tax_scheme="annual_filing",
        subject_matter="general_income",
        scenario_key="standard",
        fields=[
            RequirementFieldOut(
                field_key="rate",
                value="5% - 40%",
                confidence=1.0,
            )
        ],
    )
    _upsert_requirement(
        session,
        row2,
        country="TW",
        tax_key="tw_income",
        document=None,
        allowed_nodes=set(),
        model="test-model",
    )
    session.commit()

    reqs2 = session.query(TaxRequirement).all()
    # Still 1 row! No duplicate created
    assert len(reqs2) == 1
    assert reqs2[0].id == req1.id
    # Descriptions are updated to the latest run
    assert reqs2[0].scenario == "個人（中華民國境內居住者）取得中華民國來源所得"
    assert reqs2[0].taxpayer_role == "個人 - 綜合所得稅納稅義務人"


def test_stats_requirements_deduplicates_by_identity_key(session, tw_law, monkeypatch):
    """驗證 extract_for_document 回傳的 stats['requirements'] 確實以 identity_key 去重."""
    from taxwatch.requirements.extract import extract_for_document
    from taxwatch.requirements.schema import (
        ProvisionCitation,
        RequirementFieldOut,
        RequirementOut,
        RequirementSetOut,
    )

    # 模擬 LLM 回傳兩筆情境：文字不同但受控維度相同
    row1 = RequirementOut(
        scenario="綜合所得稅：中華民國境內居住之個人",
        taxpayer_role="一般納稅人（居住者）",
        taxpayer_class="resident_individual",
        tax_scheme="annual_filing",
        subject_matter="general_income",
        scenario_key="standard",
        fields=[
            RequirementFieldOut(
                field_key="rate",
                value="5% - 40%",
                citations=[ProvisionCitation(node_key="所得稅法#2", quote="所得稅之納稅義務人")],
                confidence=1.0,
            )
        ],
    )
    row2 = RequirementOut(
        scenario="個人（中華民國境內居住者）取得中華民國來源所得",
        taxpayer_role="個人 - 綜合所得稅納稅義務人",
        taxpayer_class="resident_individual",
        tax_scheme="annual_filing",
        subject_matter="general_income",
        scenario_key="standard",
        fields=[
            RequirementFieldOut(
                field_key="rate",
                value="5% - 40%",
                citations=[ProvisionCitation(node_key="所得稅法#2", quote="所得稅之納稅義務人")],
                confidence=1.0,
            )
        ],
    )

    mock_client = MagicMock()
    mock_client.generate_structured.return_value = RequirementSetOut(
        requirements=[row1, row2],
        unresolved=[],
    )
    mock_client.model = "mock-model"
    monkeypatch.setattr("taxwatch.requirements.extract.get_llm_client", lambda: mock_client)

    stats = extract_for_document(
        session,
        "tw-income-tax-act",
        country="TW",
        tax_key="tw_income",
    )
    # requirements_emitted 是 2 筆，但 requirements（去重後）必須是 1 筆！
    assert stats["requirements_emitted"] == 2
    assert stats["requirements"] == 1


def test_upsert_empty_identity_key_fallback(session):
    """5. identity_key 為空 → 沿用舊的 (scenario, taxpayer_role) 查找."""
    from taxwatch.models import TaxRequirement
    from taxwatch.requirements.extract import _upsert_requirement
    from taxwatch.requirements.schema import RequirementFieldOut, RequirementOut

    row = RequirementOut(
        scenario="加值型營業稅一般情境",
        taxpayer_role="營業人",
        # Empty dimensions
        fields=[
            RequirementFieldOut(
                field_key="rate",
                value="5%",
                confidence=1.0,
            )
        ],
    )
    _upsert_requirement(
        session,
        row,
        country="CN",
        tax_key="cn_vat",
        document=None,
        allowed_nodes=set(),
        model="test-model",
    )
    session.commit()

    req = session.query(TaxRequirement).filter_by(scenario="加值型營業稅一般情境").first()
    assert req is not None
    assert req.identity_key == ""
    assert req.dimensions == {}


def test_unknown_or_missing_dimensions_handling(session):
    """6. 未知或缺漏維度值 → 該列仍寫入、identity_key 為空、回報 unknowns/missing、標記 needs_review."""
    from taxwatch.models import TaxRequirement
    from taxwatch.requirements.extract import _upsert_requirement
    from taxwatch.requirements.schema import RequirementFieldOut, RequirementOut

    row = RequirementOut(
        scenario="未知維度測試情境",
        taxpayer_role="特殊納稅人",
        taxpayer_class="resident_individual",
        tax_scheme="invalid_unknown_scheme",  # Invalid
        subject_matter="general_income",
        scenario_key="",  # Missing
        fields=[
            RequirementFieldOut(
                field_key="rate",
                value="10%",
                confidence=1.0,
            )
        ],
    )
    dropped, uncited, unknowns, missing = _upsert_requirement(
        session,
        row,
        country="TW",
        tax_key="tw_income",
        document=None,
        allowed_nodes=set(),
        model="test-model",
    )
    session.commit()

    assert len(unknowns) == 1
    assert unknowns[0] == ("tax_scheme", "invalid_unknown_scheme")
    assert len(missing) == 1
    assert missing[0] == "scenario_key"

    req = session.query(TaxRequirement).filter_by(scenario="未知維度測試情境").first()
    assert req is not None
    # Identity key must be empty
    assert req.identity_key == ""
    # Fields must be marked needs_review with issue description
    for f in req.fields:
        assert f.needs_review is True
        assert "含未知的身分維度值" in f.review_reason
        assert "身分維度值缺漏" in f.review_reason


def test_real_world_tw_income_identity_convergence():
    """7. 以兩次實測的真實措辭為輸入，斷言它們產生相同的 identity_key."""
    # 第一次實測的兩組真實情境
    first_run_resident = {
        "taxpayer_class": "resident_individual",
        "tax_scheme": "annual_filing",
        "subject_matter": "general_income",
        "scenario_key": "standard",
    }
    first_run_enterprise = {
        "taxpayer_class": "domestic_enterprise",
        "tax_scheme": "annual_filing",
        "subject_matter": "general_income",
        "scenario_key": "standard",
    }

    # 第二次實測的兩組真實情境
    second_run_resident = {
        "taxpayer_class": "resident_individual",
        "tax_scheme": "annual_filing",
        "subject_matter": "general_income",
        "scenario_key": "standard",
    }
    second_run_enterprise = {
        "taxpayer_class": "domestic_enterprise",
        "tax_scheme": "annual_filing",
        "subject_matter": "general_income",
        "scenario_key": "standard",
    }

    key_res_1 = compute_identity_key(first_run_resident)
    key_res_2 = compute_identity_key(second_run_resident)
    assert key_res_1 == "resident_individual|annual_filing|general_income|standard"
    assert key_res_1 == key_res_2

    key_ent_1 = compute_identity_key(first_run_enterprise)
    key_ent_2 = compute_identity_key(second_run_enterprise)
    assert key_ent_1 == "domestic_enterprise|annual_filing|general_income|standard"
    assert key_ent_1 == key_ent_2


def test_backfill_identity_keys_script(session):
    """8. 遷移腳本可重複執行，且無法判定維度的列保持空白."""
    from scripts.backfill_identity_keys import backfill_identity_keys
    from taxwatch.models import TaxRequirement

    req1 = TaxRequirement(
        country="TW",
        tax_key="tw_income",
        scenario="綜合所得稅：中華民國境內居住之個人",
        taxpayer_role="一般納稅人（居住者）",
        identity_key="",
        dimensions={},
    )
    req2 = TaxRequirement(
        country="TW",
        tax_key="tw_income",
        scenario="營利事業（總機構在中華民國境內）",
        taxpayer_role="營利事業 - 總機構在境內",
        identity_key="",
        dimensions={},
    )
    req_undetermined = TaxRequirement(
        country="TW",
        tax_key="tw_income",
        scenario="完全無法識別的特殊自訂情境",
        taxpayer_role="不明角色",
        identity_key="",
        dimensions={},
    )
    req_cn = TaxRequirement(
        country="CN",
        tax_key="cn_vat",
        scenario="一般納稅人一般計稅",
        taxpayer_role="一般納稅人",
        identity_key="",
        dimensions={},
    )
    session.add_all([req1, req2, req_undetermined, req_cn])
    session.commit()

    # Dry-run
    stats_preview = backfill_identity_keys(session, dry_run=True)
    assert stats_preview["updated"] == 2
    assert stats_preview["skipped_undetermined"] == 2

    # Execute
    stats_exec = backfill_identity_keys(session, dry_run=False)
    assert stats_exec["updated"] == 2

    session.refresh(req1)
    session.refresh(req2)
    session.refresh(req_undetermined)
    session.refresh(req_cn)

    assert req1.identity_key == "resident_individual|annual_filing|general_income|standard"
    assert req2.identity_key == "domestic_enterprise|annual_filing|general_income|standard"
    assert req_undetermined.identity_key == ""
    assert req_cn.identity_key == ""

    # Re-run (idempotence)
    stats_rerun = backfill_identity_keys(session, dry_run=False)
    assert stats_rerun["updated"] == 0
    assert stats_rerun["skipped_already_set"] == 2


def test_cli_extract_requirements_dry_run_and_unknown_dimensions(session, tw_law, monkeypatch):
    """驗證 CLI 執行 extract-requirements 的 --dry-run 格式與未知維度值警示."""
    from typer.testing import CliRunner

    from taxwatch.cli import app
    from taxwatch.requirements.schema import (
        ProvisionCitation,
        RequirementFieldOut,
        RequirementOut,
        RequirementSetOut,
    )

    runner = CliRunner()

    row1 = RequirementOut(
        scenario="個人（境內居住者）綜所稅申報",
        taxpayer_role="個人 - 納稅義務人",
        taxpayer_class="resident_individual",
        tax_scheme="annual_filing",
        subject_matter="general_income",
        scenario_key="standard",
        fields=[
            RequirementFieldOut(
                field_key="rate",
                value="5% - 40%",
                citations=[ProvisionCitation(node_key="所得稅法#2", quote="所得稅之納稅義務人")],
                confidence=1.0,
            )
        ],
    )

    mock_client = MagicMock()
    mock_client.generate_structured.return_value = RequirementSetOut(
        requirements=[row1],
        unresolved=[],
    )
    mock_client.model = "mock-model"
    monkeypatch.setattr("taxwatch.requirements.extract.get_llm_client", lambda: mock_client)
    monkeypatch.setattr("taxwatch.db.get_session", lambda: session)
    monkeypatch.setattr("taxwatch.db.init_db", lambda: None)
    monkeypatch.setattr(session, "close", lambda: None)

    result = runner.invoke(
        app,
        [
            "extract-requirements",
            "tw-income-tax-act",
            "--country",
            "TW",
            "--tax-key",
            "tw_income",
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    assert "resident_individual|annual_filing|general_income|standard" in result.output
    assert "個人（境內居住者）綜所稅申報 / 個人 - 納稅義務人" in result.output
