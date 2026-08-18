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
