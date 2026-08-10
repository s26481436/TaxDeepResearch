"""Tests for diff engine and severity classification."""
from pathlib import Path

from taxwatch.connectors.base import RawDocument
from taxwatch.diff.classify import classify_severity
from taxwatch.diff.engine import diff_provisions
from taxwatch.models import Severity
from taxwatch.normalize.tw_law_json import TwLawJsonNormalizer

FIXTURES = Path(__file__).parent / "fixtures"


def _load_provisions(filename: str):
    raw_content = (FIXTURES / filename).read_bytes()
    raw = RawDocument(external_id="G0340001", content=raw_content, content_type="application/json")
    normalizer = TwLawJsonNormalizer()
    return normalizer.normalize(raw).provisions


def test_no_diff_on_identical():
    v1 = _load_provisions("tw_law_sample.json")
    diffs = diff_provisions(v1, v1)
    assert len(diffs) == 0


def test_detect_modification():
    v1 = _load_provisions("tw_law_sample.json")
    v2 = _load_provisions("tw_law_sample_v2.json")
    diffs = diff_provisions(v1, v2)

    modified = [d for d in diffs if d.change_type == "modified"]
    assert len(modified) >= 1

    modified_keys = {d.node_key for d in modified}
    assert "所得稅法#2" in modified_keys
    assert "所得稅法#14" in modified_keys


def test_detect_added_provision():
    v1 = _load_provisions("tw_law_sample.json")
    v2 = _load_provisions("tw_law_sample_v2.json")
    diffs = diff_provisions(v1, v2)

    added = [d for d in diffs if d.change_type == "added"]
    assert len(added) >= 1
    added_keys = {d.node_key for d in added}
    assert "所得稅法#14-1" in added_keys


def test_unchanged_article_not_in_diff():
    v1 = _load_provisions("tw_law_sample.json")
    v2 = _load_provisions("tw_law_sample_v2.json")
    diffs = diff_provisions(v1, v2)

    diff_keys = {d.node_key for d in diffs}
    assert "所得稅法#1" not in diff_keys


def test_severity_added_is_critical():
    v1 = _load_provisions("tw_law_sample.json")
    v2 = _load_provisions("tw_law_sample_v2.json")
    diffs = diff_provisions(v1, v2)

    added = next(d for d in diffs if d.change_type == "added")
    assert classify_severity(added) == Severity.CRITICAL


def test_rename_detection():
    from taxwatch.normalize.base import ProvisionData

    long_text = "這是一段很長的條文內容用來測試重新編號的偵測功能。"
    old = [ProvisionData(node_key="法#1", heading="第1條", text=long_text)]
    new = [ProvisionData(node_key="法#2", heading="第2條", text=long_text)]

    diffs = diff_provisions(old, new, rename_threshold=0.9)
    assert len(diffs) == 1
    assert diffs[0].change_type == "renumbered"
    assert diffs[0].node_key == "法#2"
