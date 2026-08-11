"""Import of the 申報規範 spreadsheet.

The header row below is the one finance actually uses, copied verbatim —
including the line break inside 「納稅/扣繳/代徵\n角色」 and the parenthetical
in 「應納稅額計算公式 (財務簡式)」. Header matching is substring-based and
order-sensitive, so this is exactly the kind of thing that silently maps a
column to the wrong field; pinning the real header is the only way to know.
"""

from __future__ import annotations

import pytest

from taxwatch.models import FieldSource, RequirementField, TaxRequirement
from taxwatch.requirements.importer import _map_columns, import_workbook

openpyxl = pytest.importorskip("openpyxl")

# Verbatim from the finance spreadsheet.
HEADER = [
    "稅種",
    "子項目/課稅情境",
    "納稅/扣繳/代徵\n角色",
    "Requirement",
    "課稅事件/觸發時點",
    "法定稅率/徵收率/費率",
    "應稅項目分類與說明",
    "應納稅額計算公式 (財務簡式)",
    "稅基/課稅基礎",
    "扣除/扣抵/抵免/減免/不得扣抵",
    "租稅優惠/減免",
    "申報期限",
    "繳款期限/開徵期間",
    "徵收管理（納稅/申報/繳納對象、地點/平台、書表/憑證/附件）",
]

VAT_ROW = [
    "增值稅",
    "一般貨物及勞務銷售",
    "一般納稅人 - 一般計稅",
    "年應稅銷售額 > 500 萬元",
    "貨物移送當天或取得銷售款當天",
    "13%",
    "銷售貨物（鋼材、木材、水泥、塑料製品）",
    "銷項稅額 = 銷售額 × 13%\n應納稅額 = 銷項稅額 - 進項稅額",
    "稅基 = 銷售額（不含增值稅）",
    "購進原物料之進項稅額，憑增值稅專用發票予以抵扣。",
    "不适用特殊税收优惠政策。",
    "以1個月或1個季度為納稅期，期滿之日起15日內辦理納稅申報。",
    "期滿之日起15日內繳納；滯納金按日加收萬分之五。",
    "向機構所在地主管稅務機關申報。需保存銷貨憑證、購進發票、會計憑證。",
]

EXPECTED_MAPPING = {
    0: "_tax",
    1: "_scenario",
    2: "_role",
    3: "applicability",
    4: "taxable_event",
    5: "rate",
    6: "taxable_items",
    7: "formula",
    8: "tax_base",
    9: "deductions",
    10: "incentives",
    11: "filing_deadline",
    12: "payment_deadline",
    13: "administration",
}


def _workbook(tmp_path, rows: list[list[str]], name: str = "申報規範.xlsx"):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    path = tmp_path / name
    workbook.save(path)
    return path


class TestHeaderMapping:
    def test_every_column_maps_to_the_intended_field(self):
        assert _map_columns(HEADER) == EXPECTED_MAPPING

    def test_rate_column_does_not_swallow_the_formula_column(self):
        """「法定稅率」 and 「應納稅額計算公式」 both contain 稅-prefixed words."""
        mapping = _map_columns(HEADER)
        assert mapping[5] == "rate"
        assert mapping[7] == "formula"

    def test_administration_column_is_not_read_as_filing_deadline(self):
        """Its text contains 「申報」 — but 「申報期限」 is a different column."""
        assert _map_columns(HEADER)[13] == "administration"

    def test_line_break_inside_a_header_is_tolerated(self):
        assert _map_columns(["納稅/扣繳/代徵\n角色"]) == {0: "_role"}

    def test_unknown_column_is_left_unmapped_rather_than_guessed(self):
        mapping = _map_columns([*HEADER, "備註"])
        assert len(mapping) == len(HEADER)


class TestImport:
    def test_imports_the_vat_row(self, session, tmp_path):
        stats = import_workbook(session, _workbook(tmp_path, [HEADER, VAT_ROW]))

        assert stats["imported"] == 1
        assert stats["skipped"] == 0

        requirement = session.query(TaxRequirement).one()
        assert requirement.tax_key == "vat"
        assert requirement.scenario == "一般貨物及勞務銷售"
        assert requirement.taxpayer_role == "一般納稅人 - 一般計稅"

        values = {f.field_key: f.value for f in requirement.fields}
        assert values["rate"] == "13%"
        assert values["applicability"] == "年應稅銷售額 > 500 萬元"
        assert "銷項稅額 = 銷售額 × 13%" in values["formula"]
        assert "萬分之五" in values["payment_deadline"]

    def test_imported_cells_are_flagged_as_untracked(self, session, tmp_path):
        """Imported prose has no provision behind it, so staleness cannot see it."""
        import_workbook(session, _workbook(tmp_path, [HEADER, VAT_ROW]))

        for field in session.query(RequirementField).all():
            assert field.source == FieldSource.IMPORT
            assert field.citations == []
            assert field.needs_review is True
            assert "尚未對應條文" in field.review_reason

    def test_reimport_updates_in_place(self, session, tmp_path):
        import_workbook(session, _workbook(tmp_path, [HEADER, VAT_ROW]))

        corrected = list(VAT_ROW)
        corrected[5] = "13%（2026 年起調整為 12%）"
        import_workbook(session, _workbook(tmp_path, [HEADER, corrected], "v2.xlsx"))

        assert session.query(TaxRequirement).count() == 1
        rate = session.query(RequirementField).filter_by(field_key="rate").one()
        assert rate.value == "13%（2026 年起調整為 12%）"

    def test_blank_cells_do_not_create_empty_fields(self, session, tmp_path):
        sparse = list(VAT_ROW)
        sparse[10] = ""  # 租稅優惠 left blank
        import_workbook(session, _workbook(tmp_path, [HEADER, sparse]))

        stored = {f.field_key for f in session.query(RequirementField).all()}
        assert "incentives" not in stored

    def test_row_without_a_scenario_is_skipped(self, session, tmp_path):
        headerless = list(VAT_ROW)
        headerless[1] = ""
        stats = import_workbook(session, _workbook(tmp_path, [HEADER, headerless, VAT_ROW]))

        assert stats["imported"] == 1
        assert stats["skipped"] == 1

    def test_sheet_without_a_scenario_column_is_rejected(self, session, tmp_path):
        with pytest.raises(ValueError, match="課稅情境"):
            import_workbook(session, _workbook(tmp_path, [["稅種", "稅率"], ["增值稅", "13%"]]))

    def test_extraction_does_not_overwrite_imported_cells(self, session, tmp_path):
        """A person filled this in; a later model run does not get to disagree."""
        from unittest.mock import MagicMock, patch

        from taxwatch.requirements.extract import extract_for_document
        from taxwatch.requirements.schema import (
            RequirementFieldOut,
            RequirementOut,
            RequirementSetOut,
        )

        import_workbook(session, _workbook(tmp_path, [HEADER, VAT_ROW]))

        # Same identity as the imported row, so extraction lands on it.
        from taxwatch.models import DocType, Document, ProvisionNode, Snapshot, Source

        source = session.query(Source).filter_by(key="cn-chinatax").first() or Source(
            key="cn-chinatax", country="CN", connector="cn_chinatax"
        )
        session.add(source)
        session.flush()
        doc = Document(
            source_id=source.id,
            external_id="cn-vat-law",
            doc_type=DocType.STATUTE,
            title="中华人民共和国增值税法",
        )
        session.add(doc)
        session.flush()
        snapshot = Snapshot(document_id=doc.id, content_hash="v1")
        session.add(snapshot)
        session.flush()
        session.add(
            ProvisionNode(
                snapshot_id=snapshot.id,
                node_key="增值税法#2",
                heading="第2条",
                text="销售货物，税率为百分之十三。",
                text_hash="h",
            )
        )
        session.commit()

        client = MagicMock(model="test-model")
        client.generate_structured.return_value = RequirementSetOut(
            requirements=[
                RequirementOut(
                    scenario="一般貨物及勞務銷售",
                    taxpayer_role="一般納稅人 - 一般計稅",
                    fields=[RequirementFieldOut(field_key="rate", value="模型覆蓋值")],
                )
            ]
        )
        with patch("taxwatch.requirements.extract.get_llm_client", return_value=client):
            extract_for_document(session, "cn-vat-law")

        rate = session.query(RequirementField).filter_by(field_key="rate").one()
        assert rate.value == "13%"
        assert rate.source == FieldSource.IMPORT
