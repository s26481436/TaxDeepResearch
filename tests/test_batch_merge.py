"""Folding provisions into one scenario must accumulate, not overwrite.

The shared scenario list works: ten batches now attach their provisions to
營利事業（總機構在中華民國境內）rather than inventing ten variants. But the
upsert replaced each cell, so that row's `deductions` kept only whatever the
last batch said — 伙食費 written, then overwritten by 棧儲費, then by 佣金 —
losing exactly the content the folding was meant to gather.
"""

from __future__ import annotations

import pytest

from taxwatch.requirements.extract import _merge_cell, _merge_citations


class TestMergeCell:
    def test_a_second_answer_is_appended(self):
        merged, conflicted = _merge_cell("伙食費依規定認列", "棧儲費依規定認列")
        assert "伙食費依規定認列" in merged
        assert "棧儲費依規定認列" in merged
        assert conflicted is True, "differing content needs a reviewer"

    def test_a_repeat_is_not_duplicated(self):
        merged, conflicted = _merge_cell("13%", "13%")
        assert merged == "13%"
        assert conflicted is False

    def test_a_substring_is_not_appended(self):
        merged, _ = _merge_cell("稅率為 13%，另有 9% 與 6%", "13%")
        assert merged == "稅率為 13%，另有 9% 與 6%"

    def test_empty_incoming_leaves_the_cell_alone(self):
        assert _merge_cell("13%", "   ") == ("13%", False)

    def test_empty_existing_takes_the_incoming(self):
        assert _merge_cell("", "13%") == ("13%", False)


class TestMergeCitations:
    def test_union_by_node_key(self):
        merged = _merge_citations(
            [{"node_key": "所得稅法#24", "quote": "a"}],
            [{"node_key": "所得稅法#25", "quote": "b"}],
        )
        assert [c["node_key"] for c in merged] == ["所得稅法#24", "所得稅法#25"]

    def test_a_repeated_provision_is_kept_once(self):
        merged = _merge_citations(
            [{"node_key": "所得稅法#24", "quote": "a"}],
            [{"node_key": "所得稅法#24", "quote": "different quote"}],
        )
        assert len(merged) == 1
        assert merged[0]["quote"] == "a", "first sighting wins"

    def test_handles_empty_sides(self):
        assert _merge_citations([], []) == []
        assert _merge_citations(None, [{"node_key": "x"}]) == [{"node_key": "x"}]


# --- reporting -------------------------------------------------------------


def test_count_and_preview_report_distinct_identities(session, monkeypatch):
    """Ten batches folding into one row is one requirement, not ten."""
    import hashlib
    from datetime import datetime
    from unittest.mock import MagicMock, patch

    import taxwatch.requirements.extract as ext
    from taxwatch.models import DocType, Document, ProvisionNode, Snapshot, Source
    from taxwatch.requirements.schema import RequirementOut, RequirementSetOut

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
    for i in range(1, 5):
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

    monkeypatch.setattr(ext, "_batch_chars", lambda: 300)

    client = MagicMock(model="m")
    client.generate_structured.return_value = RequirementSetOut(
        requirements=[
            RequirementOut(scenario="一般貨物銷售", taxpayer_role="一般納稅人", fields=[])
        ],
        unresolved=[],
    )

    with patch("taxwatch.requirements.extract.get_llm_client", return_value=client):
        stats = ext.extract_for_document(session, "cn-vat-law", dry_run=True)

    assert stats["batches"] > 1, "test needs several batches"
    assert stats["requirements_emitted"] == stats["batches"]
    assert stats["requirements"] == 1
    assert len(stats["preview"]) == 1
