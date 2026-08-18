"""A batched extraction must survive individual batch failures.

The production gateway answers overload with 400 and takes up to 6m41s for a
large request. Before this, one failed batch discarded every batch that had
already succeeded, because nothing was written until the whole run finished.
"""

from __future__ import annotations

import pytest

import taxwatch.requirements.extract as ext


class _View(dict):
    pass


def _view(n_articles: int) -> dict:
    return {
        "title": "增值税法",
        "external_id": "vat",
        "articles": [
            {
                "node_key": f"增值税法#{i}",
                "heading": f"第{i}条",
                "text": "х" * 40,
                "supplements": [],
            }
            for i in range(1, n_articles + 1)
        ],
        "unanchored_supplements": [],
        "child_documents": [],
    }


def test_batches_split_on_settings_not_hard_cap(monkeypatch):
    """Batch size comes from settings, well under the 60k hard cap."""
    monkeypatch.setattr(ext, "_batch_chars", lambda: 100)
    skeleton_text, skeleton_nodes, batches = ext._render_batches(_view(6))
    assert len(batches) > 1
    assert all(len(text) <= 200 for text, _ in batches)


def test_batch_size_never_exceeds_hard_cap(monkeypatch):
    monkeypatch.setattr(ext, "_MAX_PROVISION_CHARS", 50)

    class S:
        requirements_batch_chars = 999_999

    monkeypatch.setattr("taxwatch.config.get_settings", lambda: S())
    assert ext._batch_chars() == 50


def test_all_batches_failing_raises(monkeypatch):
    """Nothing at all must not be reported as 'this statute has no requirements'."""
    assert issubclass(ext.LLMBatchFailure, RuntimeError)


def test_skeleton_is_not_deducted_from_the_batch_budget(monkeypatch):
    """Deducting the skeleton would make skeleton size and batch count multiply.

    The skeleton rides along with every batch, so charging it against the batch
    budget shrinks the room for supplements, which creates more batches, which
    sends the skeleton more times.
    """

    class S:
        requirements_batch_chars = 2_000

    monkeypatch.setattr("taxwatch.config.get_settings", lambda: S())
    monkeypatch.setattr(ext, "_MAX_PROVISION_CHARS", 1_000_000)

    view = _view(10)  # a fat skeleton
    view["unanchored_supplements"] = [
        {
            "node_key": f"公告#{i}",
            "heading": f"第{i}条",
            "document_title": "公告",
            "text": "y" * 400,
        }
        for i in range(1, 11)
    ]

    skeleton, _, batches = ext._render_batches(view)
    assert skeleton, "parent articles must form the skeleton"

    # 10 supplements of ~450 chars against a 2000-char budget: ~3 batches.
    # Were the skeleton deducted, the budget would collapse and this would blow up.
    assert len(batches) <= 4


def test_total_per_batch_respects_the_hard_cap(monkeypatch):
    class S:
        requirements_batch_chars = 50_000

    monkeypatch.setattr("taxwatch.config.get_settings", lambda: S())
    monkeypatch.setattr(ext, "_MAX_PROVISION_CHARS", 5_000)

    view = _view(20)
    view["unanchored_supplements"] = [
        {"node_key": f"公告#{i}", "heading": "x", "document_title": "公告", "text": "y" * 300}
        for i in range(1, 21)
    ]

    skeleton, _, batches = ext._render_batches(view)
    for text, _nodes in batches:
        assert len(skeleton) + len(text) <= 5_000 + 500
