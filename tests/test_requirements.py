"""Tests for the 申報規範 matrix.

Modelled on the 增值稅 row finance supplied: 一般納稅人 / 一般計稅 / 一般貨物及
勞務銷售, 13%, 期滿之日起15日內申報, 依據《增值稅法》第三十二條.

The behaviour that matters is not "can an LLM fill a table" — it is that a cell
says which provisions it rests on, and stops claiming to be current the moment
one of them moves.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from taxwatch.models import (
    Change,
    ChangeType,
    DocType,
    Document,
    FieldSource,
    ProvisionNode,
    RequirementField,
    RequirementStatus,
    Severity,
    Snapshot,
    Source,
    TaxRequirement,
)
from taxwatch.requirements.extract import (
    CountryMismatch,
    MissingParentLaw,
    extract_for_document,
)
from taxwatch.requirements.fields import DERIVABLE_FIELD_KEYS, FIELD_KEYS
from taxwatch.requirements.schema import (
    ProvisionCitation,
    RequirementFieldOut,
    RequirementOut,
    RequirementSetOut,
)
from taxwatch.requirements.staleness import clear_review_flag, flag_stale_fields
from taxwatch.services import requirements as svc

VAT_ARTICLES = {
    "增值税法#1": "在中华人民共和国境内销售货物、服务的单位和个人，为增值税的纳税人。",
    "增值税法#2": "增值税税率：销售货物，税率为百分之十三。",
    "增值税法#32": "增值税的纳税期限分别为十日、十五日、一个月或者一个季度。"
    "以一个月或者一个季度为一个纳税期的，自期满之日起十五日内申报纳税。",
}


@pytest.fixture
def vat_law(session):
    source = Source(key="cn-chinatax", country="CN", connector="cn_chinatax")
    session.add(source)
    session.flush()

    doc = Document(
        source_id=source.id,
        external_id="cn-vat-law",
        doc_type=DocType.STATUTE,
        title="中华人民共和国增值税法",
        issued_at=datetime(2024, 12, 25),
    )
    session.add(doc)
    session.flush()

    snapshot = Snapshot(
        document_id=doc.id,
        content_hash="vat-v1",
        issued_at=datetime(2024, 12, 25),
        fetched_at=datetime(2026, 8, 11),
    )
    session.add(snapshot)
    session.flush()

    for node_key, text in VAT_ARTICLES.items():
        session.add(
            ProvisionNode(
                snapshot_id=snapshot.id,
                node_key=node_key,
                heading=f"第{node_key.split('#')[1]}条",
                text=text,
                text_hash=hashlib.sha256(text.encode()).hexdigest(),
            )
        )
    session.commit()
    return doc


def _model_output(fields: list[RequirementFieldOut]) -> RequirementSetOut:
    return RequirementSetOut(
        requirements=[
            RequirementOut(
                scenario="一般貨物及勞務銷售",
                taxpayer_role="一般納稅人 - 一般計稅",
                fields=fields,
            )
        ],
        unresolved=[],
    )


def _extract(session, output: RequirementSetOut, external_id: str = "cn-vat-law") -> dict:
    client = MagicMock(model="test-model")
    client.generate_structured.return_value = output
    with patch("taxwatch.requirements.extract.get_llm_client", return_value=client):
        return extract_for_document(session, external_id)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


class TestExtraction:
    def test_stores_a_cited_field(self, session, vat_law):
        stats = _extract(
            session,
            _model_output(
                [
                    RequirementFieldOut(
                        field_key="rate",
                        value="13%",
                        confidence=0.95,
                        citations=[
                            ProvisionCitation(
                                node_key="增值税法#2",
                                title="中华人民共和国增值税法",
                                quote="销售货物，税率为百分之十三。",
                            )
                        ],
                    )
                ]
            ),
        )

        assert stats["requirements"] == 1
        assert stats["tax_key"] == "vat"

        field = session.query(RequirementField).filter_by(field_key="rate").one()
        assert field.value == "13%"
        assert field.cited_node_keys == ["增值税法#2"]
        assert field.needs_review is False
        assert field.confidence == 0.95

    def test_drops_citations_to_provisions_never_supplied(self, session, vat_law):
        """The model may misread a provision; it may not invent one."""
        stats = _extract(
            session,
            _model_output(
                [
                    RequirementFieldOut(
                        field_key="rate",
                        value="13%",
                        confidence=0.9,
                        citations=[ProvisionCitation(node_key="增值税法#999")],
                    )
                ]
            ),
        )

        assert stats["dropped_citations"] == 1
        field = session.query(RequirementField).filter_by(field_key="rate").one()
        assert field.citations == []

    def test_uncited_field_is_zero_confidence_and_flagged(self, session, vat_law):
        _extract(
            session,
            _model_output(
                [
                    RequirementFieldOut(
                        field_key="rate",
                        value="13%",
                        confidence=0.99,  # the model's own opinion of itself
                        citations=[],
                    )
                ]
            ),
        )

        field = session.query(RequirementField).filter_by(field_key="rate").one()
        assert field.confidence == 0.0
        assert field.needs_review is True
        assert "無條文依據" in field.review_reason

    def test_ignores_field_keys_outside_the_spec(self, session, vat_law):
        _extract(
            session,
            _model_output(
                [
                    RequirementFieldOut(field_key="made_up_column", value="x"),
                    RequirementFieldOut(field_key="rate", value="13%"),
                ]
            ),
        )

        stored = {f.field_key for f in session.query(RequirementField).all()}
        assert stored == {"rate"}

    def test_duplicate_field_key_keeps_the_first(self, session, vat_law):
        """A malformed response naming the same column twice must not crash the run."""
        stats = _extract(
            session,
            _model_output(
                [
                    RequirementFieldOut(
                        field_key="rate",
                        value="13%",
                        confidence=0.95,
                        citations=[ProvisionCitation(node_key="增值税法#2")],
                    ),
                    RequirementFieldOut(
                        field_key="rate",
                        value="幻覺",
                        confidence=0.9,
                        citations=[ProvisionCitation(node_key="增值税法#888")],
                    ),
                ]
            ),
        )

        assert stats["requirements"] == 1
        field = session.query(RequirementField).filter_by(field_key="rate").one()
        assert field.value == "13%"

    def test_re_extraction_does_not_overwrite_a_human_edit(self, session, vat_law):
        _extract(session, _model_output([RequirementFieldOut(field_key="rate", value="13%")]))
        requirement = session.query(TaxRequirement).one()
        svc.update_field(session, requirement.id, "rate", "13%（另有9%與6%低稅率）")

        _extract(
            session,
            _model_output([RequirementFieldOut(field_key="rate", value="13%")]),
        )

        field = session.query(RequirementField).filter_by(field_key="rate").one()
        assert field.value == "13%（另有9%與6%低稅率）"
        assert field.source == FieldSource.MANUAL

    def test_re_extraction_updates_llm_authored_cells(self, session, vat_law):
        _extract(session, _model_output([RequirementFieldOut(field_key="rate", value="13%")]))
        _extract(session, _model_output([RequirementFieldOut(field_key="rate", value="9%")]))

        assert session.query(RequirementField).filter_by(field_key="rate").one().value == "9%"

    def test_dry_run_writes_nothing(self, session, vat_law):
        client = MagicMock(model="test-model")
        client.generate_structured.return_value = _model_output(
            [RequirementFieldOut(field_key="rate", value="13%")]
        )
        with patch("taxwatch.requirements.extract.get_llm_client", return_value=client):
            stats = extract_for_document(session, "cn-vat-law", dry_run=True)

        assert stats["preview"][0]["scenario"] == "一般貨物及勞務銷售"
        assert session.query(TaxRequirement).count() == 0

    def test_derives_country_from_document_source_by_default(self, session, vat_law):
        """Extract without specifying country derives it from Document.source."""
        # Create a TW source and law
        tw_source = Source(key="tw-law", country="TW", connector="tw_moj_law")
        session.add(tw_source)
        session.flush()

        tw_doc = Document(
            source_id=tw_source.id,
            external_id="tw-business-tax-law",
            doc_type=DocType.STATUTE,
            title="加值型及非加值型營業稅法",
            issued_at=datetime(2024, 1, 1),
        )
        session.add(tw_doc)
        session.flush()

        tw_snapshot = Snapshot(
            document_id=tw_doc.id,
            content_hash="tw-bt-v1",
            issued_at=datetime(2024, 1, 1),
            fetched_at=datetime(2026, 8, 11),
        )
        session.add(tw_snapshot)
        session.flush()

        session.add(
            ProvisionNode(
                snapshot_id=tw_snapshot.id,
                node_key="營業稅法#1",
                heading="第1條",
                text="在中華民國境內銷售貨物或勞務及進口貨物，均應依本法規定課徵加值型或非加值型之營業稅。",
                text_hash=hashlib.sha256(b"tw").hexdigest(),
            )
        )
        session.commit()

        client = MagicMock(model="test-model")
        client.generate_structured.return_value = RequirementSetOut(
            requirements=[
                RequirementOut(
                    scenario="一般銷售",
                    taxpayer_role="營業人",
                    fields=[
                        RequirementFieldOut(
                            field_key="rate",
                            value="5%",
                            confidence=0.95,
                            citations=[
                                ProvisionCitation(
                                    node_key="營業稅法#1",
                                    title="加值型及非加值型營業稅法",
                                    quote="加值型之營業稅",
                                )
                            ],
                        )
                    ],
                )
            ],
            unresolved=[],
        )
        with patch("taxwatch.requirements.extract.get_llm_client", return_value=client):
            extract_for_document(session, "tw-business-tax-law")

        req = session.query(TaxRequirement).filter_by(taxpayer_role="營業人").one()
        assert req.country == "TW"

    def test_explicit_mismatched_country_raises_error(self, session, vat_law):
        """Specifying country=TW on a CN document raises CountryMismatch."""
        client = MagicMock(model="test-model")
        client.generate_structured.return_value = _model_output([])
        with patch("taxwatch.requirements.extract.get_llm_client", return_value=client):
            with pytest.raises(CountryMismatch) as exc_info:
                extract_for_document(session, "cn-vat-law", country="TW")

        assert exc_info.value.expected == "TW"
        assert exc_info.value.actual == "CN"


class TestOrphanedChildDocument:
    """A 实施条例 without its statute cannot support 申報規範.

    It defines the terms the statute introduces and nothing else: no 納稅義務人,
    no 課稅範圍, no 申報期限. Extracting from it produces rows resting on
    articles that were never supplied — the one failure mode this pipeline
    exists to prevent.
    """

    @pytest.fixture
    def orphan_regulation(self, session):
        source = session.query(Source).filter_by(key="cn-chinatax").first()
        if source is None:
            source = Source(key="cn-chinatax", country="CN", connector="cn_chinatax")
            session.add(source)
            session.flush()

        doc = Document(
            source_id=source.id,
            external_id="cn-vat-regulation",
            doc_type=DocType.REGULATION,
            title="中华人民共和国增值税法实施条例",
            issued_at=datetime(2025, 1, 1),
        )
        session.add(doc)
        session.flush()

        snapshot = Snapshot(
            document_id=doc.id,
            content_hash="vat-reg-v1",
            issued_at=datetime(2025, 1, 1),
            fetched_at=datetime(2026, 8, 11),
        )
        session.add(snapshot)
        session.flush()

        text = "本条例所称销售货物，是指有偿转让货物所有权。"
        session.add(
            ProvisionNode(
                snapshot_id=snapshot.id,
                node_key="增值税法实施条例#3",
                heading="第三条",
                text=text,
                text_hash=hashlib.sha256(text.encode()).hexdigest(),
            )
        )
        session.commit()
        return doc

    def test_refuses_and_names_the_statute_it_needs(self, session, orphan_regulation):
        with pytest.raises(MissingParentLaw) as caught:
            _extract(session, _model_output([]), external_id="cn-vat-regulation")
        assert caught.value.parent_key == "增值税法"
        assert caught.value.status == "missing"
        assert session.query(TaxRequirement).count() == 0

    def test_allow_child_overrides_the_refusal(self, session, orphan_regulation):
        client = MagicMock(model="test-model")
        client.generate_structured.return_value = _model_output(
            [RequirementFieldOut(field_key="rate", value="13%")]
        )
        with patch("taxwatch.requirements.extract.get_llm_client", return_value=client):
            stats = extract_for_document(session, "cn-vat-regulation", allow_child=True)

        # ...but the result carries the caveat, so no reader takes the rows as
        # resting on the statute.
        assert stats["missing_parent"] == {"key": "增值税法", "status": "missing"}
        assert stats["requirements"] == 1

    def test_the_statute_being_present_lifts_the_refusal(self, session, vat_law, orphan_regulation):
        stats = _extract(
            session,
            _model_output([RequirementFieldOut(field_key="rate", value="13%")]),
            external_id="cn-vat-regulation",
        )
        assert stats["source_document"] == "中华人民共和国增值税法"
        assert stats["missing_parent"] is None


# ---------------------------------------------------------------------------
# Staleness — the reason this lives in the system and not a spreadsheet
# ---------------------------------------------------------------------------


def _change(session, vat_law, node_key: str, severity=Severity.MAJOR) -> Change:
    snapshot = session.query(Snapshot).filter_by(document_id=vat_law.id).one()
    change = Change(
        document_id=vat_law.id,
        from_snapshot_id=snapshot.id,
        to_snapshot_id=snapshot.id,
        node_key=node_key,
        change_type=ChangeType.MODIFIED,
        diff_text="- 十五日\n+ 二十日",
        severity=severity,
        detected_at=datetime(2026, 8, 11),
    )
    session.add(change)
    session.flush()
    return change


class TestStaleness:
    @pytest.fixture
    def filing_deadline(self, session, vat_law):
        _extract(
            session,
            _model_output(
                [
                    RequirementFieldOut(
                        field_key="filing_deadline",
                        value="以一個月或一個季度為納稅期，期滿之日起15日內申報。",
                        confidence=0.95,
                        citations=[ProvisionCitation(node_key="增值税法#32")],
                    ),
                    RequirementFieldOut(
                        field_key="rate",
                        value="13%",
                        confidence=0.95,
                        citations=[ProvisionCitation(node_key="增值税法#2")],
                    ),
                ]
            ),
        )
        return session.query(RequirementField).filter_by(field_key="filing_deadline").one()

    def test_amending_a_cited_provision_flags_only_that_cell(
        self, session, vat_law, filing_deadline
    ):
        change = _change(session, vat_law, "增值税法#32")

        flagged = flag_stale_fields(session, [change])

        assert [f.field_key for f in flagged] == ["filing_deadline"]
        assert filing_deadline.needs_review is True
        assert filing_deadline.stale_change_id == change.id
        assert "增值税法#32" in filing_deadline.review_reason

        rate = session.query(RequirementField).filter_by(field_key="rate").one()
        assert rate.needs_review is False

    def test_row_status_becomes_stale(self, session, vat_law, filing_deadline):
        flag_stale_fields(session, [_change(session, vat_law, "增值税法#32")])
        assert filing_deadline.requirement.status == RequirementStatus.STALE

    def test_unrelated_provision_flags_nothing(self, session, vat_law, filing_deadline):
        assert flag_stale_fields(session, [_change(session, vat_law, "增值税法#1")]) == []

    def test_non_derivable_fields_are_never_auto_flagged(self, session, vat_law):
        """Nothing in a diff can confirm 「不適用特殊稅收優惠政策」."""
        assert "incentives" not in DERIVABLE_FIELD_KEYS

        _extract(
            session,
            _model_output(
                [
                    RequirementFieldOut(
                        field_key="incentives",
                        value="不適用特殊稅收優惠政策。",
                        confidence=0.5,
                        citations=[ProvisionCitation(node_key="增值税法#2")],
                    )
                ]
            ),
        )
        field = session.query(RequirementField).filter_by(field_key="incentives").one()
        field.needs_review = False
        session.flush()

        assert flag_stale_fields(session, [_change(session, vat_law, "增值税法#2")]) == []

    def test_reviewer_can_accept_without_editing(self, session, vat_law, filing_deadline):
        """An amendment can touch a provision without changing the obligation."""
        flag_stale_fields(session, [_change(session, vat_law, "增值税法#32")])
        original = filing_deadline.value

        clear_review_flag(session, filing_deadline)

        assert filing_deadline.needs_review is False
        assert filing_deadline.value == original
        assert filing_deadline.source == FieldSource.LLM
        assert filing_deadline.requirement.status == RequirementStatus.REVIEWED

    def test_reviewer_edit_is_recorded_as_manual(self, session, vat_law, filing_deadline):
        flag_stale_fields(session, [_change(session, vat_law, "增值税法#32")])

        clear_review_flag(session, filing_deadline, value="期滿之日起20日內申報。")

        assert filing_deadline.value == "期滿之日起20日內申報。"
        assert filing_deadline.source == FieldSource.MANUAL

    def test_row_stays_stale_while_any_cell_is_unresolved(self, session, vat_law):
        _extract(
            session,
            _model_output(
                [
                    RequirementFieldOut(
                        field_key="filing_deadline",
                        value="15日內",
                        citations=[ProvisionCitation(node_key="增值税法#32")],
                        confidence=0.9,
                    ),
                    RequirementFieldOut(
                        field_key="tax_base",
                        value="銷售額（不含增值稅）",
                        citations=[ProvisionCitation(node_key="增值税法#32")],
                        confidence=0.9,
                    ),
                ]
            ),
        )
        change = _change(session, vat_law, "增值税法#32")
        flagged = flag_stale_fields(session, [change])
        assert len(flagged) == 2

        clear_review_flag(session, flagged[0])
        assert flagged[0].requirement.status == RequirementStatus.STALE

        clear_review_flag(session, flagged[1])
        assert flagged[1].requirement.status == RequirementStatus.REVIEWED


# ---------------------------------------------------------------------------
# Service layer
# ---------------------------------------------------------------------------


class TestService:
    def test_detail_lists_every_column_including_missing_ones(self, session, vat_law):
        """A blank cell is information; hiding it overstates coverage."""
        _extract(session, _model_output([RequirementFieldOut(field_key="rate", value="13%")]))
        requirement = session.query(TaxRequirement).one()

        detail = svc.get_requirement(session, requirement.id)
        assert [f["field_key"] for f in detail["fields"]] == list(FIELD_KEYS)

        missing = [f["field_key"] for f in detail["fields"] if f["missing"]]
        assert "filing_deadline" in missing

    def test_listing_puts_rows_needing_review_first(self, session, vat_law):
        _extract(
            session,
            RequirementSetOut(
                requirements=[
                    RequirementOut(
                        scenario="A 情境",
                        fields=[
                            RequirementFieldOut(
                                field_key="rate",
                                value="13%",
                                confidence=0.9,
                                citations=[ProvisionCitation(node_key="增值税法#2")],
                            )
                        ],
                    ),
                    RequirementOut(
                        scenario="B 情境",
                        fields=[RequirementFieldOut(field_key="rate", value="9%")],
                    ),
                ]
            ),
        )

        rows = svc.list_requirements(session)
        assert rows[0]["scenario"] == "B 情境"
        assert rows[0]["fields_needing_review"] == 1

    def test_review_summary_explains_each_item(self, session, vat_law):
        _extract(
            session,
            _model_output(
                [
                    RequirementFieldOut(
                        field_key="filing_deadline",
                        value="15日內",
                        confidence=0.9,
                        citations=[ProvisionCitation(node_key="增值税法#32")],
                    )
                ]
            ),
        )
        flag_stale_fields(session, [_change(session, vat_law, "增值税法#32")])

        summary = svc.review_summary(session)
        assert summary["count"] == 1
        item = summary["items"][0]
        assert item["field_label"] == "申報期限"
        assert item["tax_name"] == "增值稅／營業稅"
        assert "增值税法#32" in item["reason"]

    def test_update_field_creates_a_cell_that_did_not_exist(self, session, vat_law):
        _extract(session, _model_output([RequirementFieldOut(field_key="rate", value="13%")]))
        requirement = session.query(TaxRequirement).one()

        detail = svc.update_field(
            session, requirement.id, "administration", "向機構所在地主管稅務機關申報。"
        )

        cell = next(f for f in detail["fields"] if f["field_key"] == "administration")
        assert cell["source"] == "manual"
        assert cell["missing"] is False

    def test_unknown_requirement(self, session):
        with pytest.raises(svc.RequirementNotFound):
            svc.get_requirement(session, 9999)


# ---------------------------------------------------------------------------
# Web + API surface
# ---------------------------------------------------------------------------


class TestWebSurface:
    @pytest.fixture
    def client(self, session, vat_law, monkeypatch):
        from fastapi.testclient import TestClient

        from taxwatch.api.routes import requirements as requirements_route
        from taxwatch.web import app as web_app

        _extract(
            session,
            _model_output(
                [
                    RequirementFieldOut(
                        field_key="rate",
                        value="13%",
                        confidence=0.95,
                        citations=[
                            ProvisionCitation(
                                node_key="增值税法#2",
                                title="中华人民共和国增值税法",
                                quote="销售货物，税率为百分之十三。",
                            )
                        ],
                    ),
                    RequirementFieldOut(field_key="incentives", value="不適用特殊稅收優惠政策。"),
                ]
            ),
        )

        monkeypatch.setattr(session, "close", lambda: None)
        for module in (web_app, requirements_route):
            monkeypatch.setattr(module, "get_session", lambda: session)
        return TestClient(web_app.app)

    def test_matrix_page(self, client):
        resp = client.get("/requirements")
        assert resp.status_code == 200
        assert "一般貨物及勞務銷售" in resp.text
        assert "待覆核" in resp.text  # the uncited incentives cell

    def test_detail_page_shows_the_quoted_provision(self, client):
        rows = client.get("/api/requirements").json()["requirements"]
        resp = client.get(f"/requirements/{rows[0]['id']}")
        assert resp.status_code == 200
        assert "增值税法#2" in resp.text
        assert "销售货物，税率为百分之十三。" in resp.text

    def test_detail_page_unknown(self, client):
        resp = client.get("/requirements/9999")
        assert resp.status_code == 200
        assert "找不到申報規範" in resp.text

    def test_api_review_queue(self, client):
        body = client.get("/api/requirements/review").json()
        assert body["count"] == 1
        assert body["items"][0]["field_key"] == "incentives"

    def test_api_update_field(self, client):
        rid = client.get("/api/requirements").json()["requirements"][0]["id"]
        resp = client.put(
            f"/api/requirements/{rid}/fields/incentives",
            json={"value": "適用小微企業免徵優惠。"},
        )
        assert resp.status_code == 200
        cell = next(f for f in resp.json()["fields"] if f["field_key"] == "incentives")
        assert cell["source"] == "manual"
        assert cell["needs_review"] is False

    def test_web_approve_clears_review_flag(self, client):
        rid = client.get("/api/requirements").json()["requirements"][0]["id"]
        resp = client.post(
            f"/requirements/{rid}/fields/incentives/review",
            data={"action": "approve"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        detail = client.get(f"/requirements/{rid}").text
        assert "待覆核" not in detail or "0 欄待覆核" in detail

    def test_web_edit_updates_value(self, client):
        rid = client.get("/api/requirements").json()["requirements"][0]["id"]
        client.post(
            f"/requirements/{rid}/fields/incentives/review",
            data={"action": "edit", "value": "已人工修改。"},
            follow_redirects=False,
        )
        detail = client.get(f"/api/requirements/{rid}").json()
        cell = next(f for f in detail["fields"] if f["field_key"] == "incentives")
        assert cell["value"] == "已人工修改。"
        assert cell["source"] == "manual"

    def test_extract_form_shown_on_requirements_page(self, client):
        resp = client.get("/requirements")
        assert "抽取申報規範" in resp.text
        assert "抽取" in resp.text

    def test_extract_post_with_bad_document_shows_error(self, client):
        resp = client.post(
            "/requirements/extract",
            data={"document": "no-such-law"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        assert "flash-err" in resp.text or "找不到" in resp.text or "no-such-law" in resp.text
