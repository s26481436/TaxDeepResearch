"""Batches must know what earlier batches already named.

Extracting 所得稅法 produced four versions of one row, each annotated with
whatever that batch happened to see:

    營利事業一般所得稅申報（含各項費用、損失、耗竭攤折之認列規範）
    營利事業一般所得稅申報（含附贈、分期付款、工程、不動產處分等收入認列規則）
    營利事業一般所得稅申報（含存貨估價、成本認列、費用損失認定）

and separately 伙食費認列, 書報雜誌費用認列, 棧儲費認列 — provisions the prompt
tells the model to fold into an existing scenario's `deductions`. It cannot
fold into a scenario it was never shown.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

import taxwatch.requirements.extract as ext
from taxwatch.models import DocType, Document, ProvisionNode, Snapshot, Source
from taxwatch.requirements.prompts import EXTRACTION_TEMPLATE
from taxwatch.requirements.schema import RequirementOut, RequirementSetOut


@pytest.fixture
def law(session):
    source = Source(key="cn-chinatax", country="CN", connector="cn_chinatax")
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
    snap = Snapshot(document_id=doc.id, content_hash="v1", issued_at=datetime(2024, 12, 25))
    session.add(snap)
    session.flush()
    for i in range(1, 7):
        text = f"第{i}条正文" * 30
        session.add(
            ProvisionNode(
                snapshot_id=snap.id,
                node_key=f"增值税法#{i}",
                heading=f"第{i}条",
                text=text,
                text_hash=hashlib.sha256(text.encode()).hexdigest(),
            )
        )
    session.commit()
    return doc


def test_first_batch_is_given_no_list():
    assert ext._known_scenarios_section([]) == ""


def test_section_names_the_scenarios_and_forbids_new_wording():
    section = ext._known_scenarios_section([("一般貨物銷售", "一般納稅人")])
    assert "一般貨物銷售" in section
    assert "一般納稅人" in section
    assert "逐字沿用" in section
    assert "不要另創說法" in section


def test_section_keeps_the_two_fields_apart():
    """A joined 「scenario｜role」 line reads as one value and gets copied whole.

    Seen in a real run: 「個人（中華民國境內居住者）綜合所得稅申報｜個人 - 綜合
    所得稅」 arrived as a brand new scenario, so the list grew instead of
    converging — 132 rows became 173.
    """
    section = ext._known_scenarios_section([("一般貨物銷售", "一般納稅人")])

    assert "一般貨物銷售｜一般納稅人" not in section
    assert "scenario: 一般貨物銷售" in section
    assert "taxpayer_role: 一般納稅人" in section
    assert "不要把它們串成一個字串" in section


@pytest.mark.parametrize("separator", ["｜", "|"])
def test_an_echoed_role_is_trimmed_back_off(separator):
    """Belt and braces: a model that concatenates anyway must not mint a new row."""
    echoed = f"一般貨物銷售{separator}一般納稅人"
    assert ext._strip_echoed_role(echoed) == "一般貨物銷售"


def test_an_ordinary_scenario_is_untouched():
    assert ext._strip_echoed_role(" 一般貨物銷售 ") == "一般貨物銷售"


def test_section_is_capped_and_says_so():
    known = [(f"情境{i}", "角色") for i in range(ext._MAX_KNOWN_SCENARIOS + 5)]
    section = ext._known_scenarios_section(known)
    assert section.count("scenario: ") == ext._MAX_KNOWN_SCENARIOS
    assert "清單已截斷" in section


def test_later_batches_receive_what_earlier_batches_named(session, law, monkeypatch):
    monkeypatch.setattr(ext, "_batch_chars", lambda: 300)  # force several batches

    prompts: list[str] = []
    client = MagicMock(model="m")

    def generate(*args, **kwargs):
        prompts.append(kwargs["user_prompt"])
        return RequirementSetOut(
            requirements=[
                RequirementOut(scenario="一般貨物銷售", taxpayer_role="一般納稅人", fields=[])
            ],
            unresolved=[],
        )

    client.generate_structured.side_effect = generate

    with patch("taxwatch.requirements.extract.get_llm_client", return_value=client):
        ext.extract_for_document(session, "cn-vat-law")

    assert len(prompts) > 1, "test needs more than one batch to be meaningful"
    assert "一般貨物銷售" not in prompts[0], "the first batch has nothing to inherit"
    assert "一般貨物銷售" in prompts[1], "the second batch must see it"


def test_the_list_carries_across_documents(session, law):
    """It lived inside one document's loop before, and reset between documents."""
    known: list[tuple[str, str]] = []
    client = MagicMock(model="m")
    client.generate_structured.return_value = RequirementSetOut(
        requirements=[RequirementOut(scenario="一般貨物銷售", taxpayer_role="一般納稅人", fields=[])],
        unresolved=[],
    )

    with patch("taxwatch.requirements.extract.get_llm_client", return_value=client):
        ext.extract_for_document(session, "cn-vat-law", known_scenarios=known)

    assert ("一般貨物銷售", "一般納稅人") in known


def test_template_has_a_slot_for_the_list():
    assert "{known_scenarios_section}" in EXTRACTION_TEMPLATE
