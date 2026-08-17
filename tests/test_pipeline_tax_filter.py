"""Tests for the --tax pipeline filter.

Core guarantees:
1. Without --tax, all refs pass through unchanged (regression guard)
2. With --tax, only documents matching the requested tax types survive
3. Layer 1 (connector keywords) is injected only when no custom keywords exist
4. Layer 2 (classifier) is the authoritative filter — consistent with the dashboard
5. Stats record the filter and filtered-out count
6. tax_keys is a *named* parameter on execute_pipeline, not swallowed by **_kwargs
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from taxwatch.connectors.base import DocumentRef, RawDocument
from taxwatch.models import Base, Source


@pytest.fixture
def db(session):
    """Alias the conftest session fixture."""
    return session


def _ref(title: str, external_id: str = "") -> DocumentRef:
    return DocumentRef(
        external_id=external_id or title[:20],
        title=title,
        doc_type="STATUTE",
        url=f"https://example.gov.cn/{title}",
        issued_at=datetime(2026, 1, 1),
    )


def _raw(ref: DocumentRef) -> RawDocument:
    return RawDocument(
        external_id=ref.external_id,
        content=b"<html>placeholder</html>",
        url=ref.url,
        metadata={"skip": True},  # skip normalisation — we only test discover+filter
    )


_REFS = [
    _ref("中华人民共和国增值税法", "增值税法"),
    _ref("中华人民共和国企业所得税法", "企业所得税法"),
    _ref("中华人民共和国印花税法", "印花税法"),
    _ref("国家税务总局关于纳税信用管理的公告", "征管公告"),
]


def _mock_sources():
    return {
        "cn-chinatax": {
            "country": "CN",
            "connector": "cn_chinatax",
            "config": {},
            "enabled": True,
        },
    }


def _mock_sources_with_keywords():
    return {
        "cn-chinatax": {
            "country": "CN",
            "connector": "cn_chinatax",
            "config": {"keywords": ["增值税", "企业所得税"]},
            "enabled": True,
        },
    }


def _mock_sources_with_tax_keys():
    return {
        "cn-chinatax": {
            "country": "CN",
            "connector": "cn_chinatax",
            "config": {},
            "enabled": True,
            "tax_keys": ["cn_vat"],
        },
    }


class FakeConnector:
    def __init__(self, source_config):
        self.source_config = source_config
        self.injected_keywords = source_config.get("keywords")

    def discover(self, since=None):
        return list(_REFS)

    def fetch(self, ref):
        return _raw(ref)


@pytest.fixture
def _patch_connector(monkeypatch):
    """Patch the connector registry so no real HTTP happens."""
    monkeypatch.setattr(
        "taxwatch.jobs.pipeline.get_connector",
        lambda connector_type, cfg: FakeConnector(cfg),
    )
    monkeypatch.setattr(
        "taxwatch.jobs.pipeline.get_normalizer",
        lambda connector_type: MagicMock(),
    )


# ---------------------------------------------------------------------------
# 1. No filter — all refs pass through
# ---------------------------------------------------------------------------


def test_no_tax_filter_passes_all_refs(db, _patch_connector, monkeypatch):
    monkeypatch.setattr("taxwatch.jobs.pipeline.load_sources", _mock_sources)
    from taxwatch.jobs.pipeline import execute_pipeline

    stats = execute_pipeline(db, "cn-chinatax", stop_after="fetch")
    assert stats["stages"]["discover"]["documents_found"] == len(_REFS)
    assert "tax_filter" not in stats["stages"]["discover"]


# ---------------------------------------------------------------------------
# 2. With --tax, only matching refs survive
# ---------------------------------------------------------------------------


def test_tax_filter_keeps_only_matching(db, _patch_connector, monkeypatch):
    monkeypatch.setattr("taxwatch.jobs.pipeline.load_sources", _mock_sources)
    from taxwatch.jobs.pipeline import execute_pipeline

    stats = execute_pipeline(
        db, "cn-chinatax", stop_after="fetch", tax_keys=["cn_vat"]
    )
    # 增值税法 should survive; others filtered out
    assert stats["stages"]["discover"]["documents_found"] == 1
    assert stats["stages"]["discover"]["filtered_out"] == 3
    assert stats["stages"]["discover"]["tax_filter"] == ["cn_vat"]


def test_tax_filter_multiple_keys(db, _patch_connector, monkeypatch):
    monkeypatch.setattr("taxwatch.jobs.pipeline.load_sources", _mock_sources)
    from taxwatch.jobs.pipeline import execute_pipeline

    stats = execute_pipeline(
        db, "cn-chinatax", stop_after="fetch", tax_keys=["cn_vat", "cn_stamp"]
    )
    assert stats["stages"]["discover"]["documents_found"] == 2
    assert stats["stages"]["discover"]["filtered_out"] == 2


# ---------------------------------------------------------------------------
# 3. Layer 1: connector keywords injection
# ---------------------------------------------------------------------------


def test_keywords_injected_when_none_set(db, _patch_connector, monkeypatch):
    """When source has no custom keywords, tax type keywords are injected."""
    monkeypatch.setattr("taxwatch.jobs.pipeline.load_sources", _mock_sources)
    connectors = []
    original_get = FakeConnector.__init__

    def spy_init(self, cfg):
        original_get(self, cfg)
        connectors.append(self)

    monkeypatch.setattr(FakeConnector, "__init__", spy_init)
    monkeypatch.setattr(
        "taxwatch.jobs.pipeline.get_connector",
        lambda connector_type, cfg: FakeConnector(cfg),
    )

    from taxwatch.jobs.pipeline import execute_pipeline

    execute_pipeline(db, "cn-chinatax", stop_after="fetch", tax_keys=["cn_vat"])
    assert connectors
    # VAT keywords should have been injected
    assert connectors[0].injected_keywords
    assert "增值税" in connectors[0].injected_keywords


def test_keywords_not_overwritten_when_already_set(
    db, _patch_connector, monkeypatch
):
    """When source already has custom keywords, don't overwrite them."""
    monkeypatch.setattr(
        "taxwatch.jobs.pipeline.load_sources", _mock_sources_with_keywords
    )
    connectors = []

    def spy_get(connector_type, cfg):
        c = FakeConnector(cfg)
        connectors.append(c)
        return c

    monkeypatch.setattr("taxwatch.jobs.pipeline.get_connector", spy_get)

    from taxwatch.jobs.pipeline import execute_pipeline

    execute_pipeline(
        db, "cn-chinatax", stop_after="fetch", tax_keys=["cn_stamp"]
    )
    assert connectors
    # Original keywords preserved, not replaced with stamp keywords
    assert connectors[0].injected_keywords == ["增值税", "企业所得税"]


# ---------------------------------------------------------------------------
# 4. sources.yaml tax_keys fallback
# ---------------------------------------------------------------------------


def test_yaml_tax_keys_used_when_cli_not_given(
    db, _patch_connector, monkeypatch
):
    monkeypatch.setattr(
        "taxwatch.jobs.pipeline.load_sources", _mock_sources_with_tax_keys
    )
    from taxwatch.jobs.pipeline import execute_pipeline

    stats = execute_pipeline(db, "cn-chinatax", stop_after="fetch")
    assert stats["stages"]["discover"]["tax_filter"] == ["cn_vat"]
    assert stats["stages"]["discover"]["documents_found"] == 1


def test_cli_tax_overrides_yaml(db, _patch_connector, monkeypatch):
    monkeypatch.setattr(
        "taxwatch.jobs.pipeline.load_sources", _mock_sources_with_tax_keys
    )
    from taxwatch.jobs.pipeline import execute_pipeline

    stats = execute_pipeline(
        db, "cn-chinatax", stop_after="fetch", tax_keys=["cn_stamp"]
    )
    assert stats["stages"]["discover"]["tax_filter"] == ["cn_stamp"]
    assert stats["stages"]["discover"]["documents_found"] == 1


# ---------------------------------------------------------------------------
# 5. Trap test: tax_keys must be a named parameter, not eaten by **_kwargs
# ---------------------------------------------------------------------------


def test_tax_keys_not_swallowed_by_kwargs(db, _patch_connector, monkeypatch):
    """Explicitly verify tax_keys has effect — the biggest trap in this change."""
    monkeypatch.setattr("taxwatch.jobs.pipeline.load_sources", _mock_sources)
    from taxwatch.jobs.pipeline import execute_pipeline

    # Without filter
    stats_all = execute_pipeline(db, "cn-chinatax", stop_after="fetch")
    # With filter
    stats_vat = execute_pipeline(
        db, "cn-chinatax", stop_after="fetch", tax_keys=["cn_vat"]
    )

    assert stats_all["stages"]["discover"]["documents_found"] > \
        stats_vat["stages"]["discover"]["documents_found"]
