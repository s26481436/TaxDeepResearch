"""Output truncation must shrink the ask, not enlarge the allowance.

Growing max_tokens makes the generation longer, and on the gateway fronting
this deployment a long generation is what returns 502. That trades a
truncation — from which the batch can be split and retried — for a timeout,
from which nothing is recovered.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

import taxwatch.requirements.extract as ext
from taxwatch.analysis.client import LLMOutputTruncated
from taxwatch.models import DocType, Document, ProvisionNode, Snapshot, Source, TaxRequirement
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
        issued_at=datetime(2024, 12, 25),
    )
    session.add(doc)
    session.flush()
    snap = Snapshot(document_id=doc.id, content_hash="v1", issued_at=datetime(2024, 12, 25))
    session.add(snap)
    session.flush()
    for i in range(1, 9):
        text = f"第{i}条正文" * 20
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


def _ok(scenario):
    return RequirementSetOut(
        requirements=[RequirementOut(scenario=scenario, taxpayer_role="一般納稅人", fields=[])],
        unresolved=[],
    )


def test_truncated_batch_is_split_and_both_halves_are_used(session, law, monkeypatch):
    monkeypatch.setattr(ext, "_batch_chars", lambda: 10_000)  # one batch of 8 blocks

    calls = {"n": 0, "sizes": []}
    client = MagicMock(model="m")

    def generate(*args, **kwargs):
        calls["n"] += 1
        calls["sizes"].append(len(kwargs["user_prompt"]))
        if calls["n"] == 1:
            raise LLMOutputTruncated("truncated")
        return _ok(f"情境{calls['n']}")

    client.generate_structured.side_effect = generate

    with patch("taxwatch.requirements.extract.get_llm_client", return_value=client):
        stats = ext.extract_for_document(session, "cn-vat-law")

    # First call covered everything and was truncated; the two halves followed.
    assert calls["n"] == 3
    assert calls["sizes"][1] < calls["sizes"][0]
    assert calls["sizes"][2] < calls["sizes"][0]
    assert stats["requirements"] == 2
    assert stats["failed_batches"] == []


def test_indivisible_batch_is_recorded_not_retried_forever(session, law, monkeypatch):
    """A single provision that still overruns cannot be split any further."""
    monkeypatch.setattr(ext, "_batch_chars", lambda: 1)  # one block per batch

    client = MagicMock(model="m")
    client.generate_structured.side_effect = LLMOutputTruncated("truncated")

    with patch("taxwatch.requirements.extract.get_llm_client", return_value=client):
        with pytest.raises(ext.LLMBatchFailure):
            ext.extract_for_document(session, "cn-vat-law")

    assert session.query(TaxRequirement).count() == 0


def test_growth_is_disabled_by_default():
    """Enlarging the budget is opt-in; it lengthens the generation."""
    from taxwatch.config import get_settings

    assert get_settings().llm_max_tokens_growth == 1.0
