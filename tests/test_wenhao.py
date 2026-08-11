"""Tests for 文號 extraction.

The cases here are drawn from the shapes that actually occur in the
國家稅務總局法規庫 — several of them were regressions found by checking the
old patterns against a real corpus.
"""
import pytest

from taxwatch.wenhao import extract_all, extract_first, normalize


@pytest.mark.parametrize("raw", [
    "国家税务总局公告2026年第6号",
    "国家税务总局通告2024年第3号",
    "财税〔2026〕15号",
    "国税发〔2003〕67号",
    "国税函发〔1998〕156号",
    "国税地字〔1994〕12号",
    "财关税〔2021〕7号",
    "财税字〔1996〕9号",
    "财综〔2019〕41号",
    "人社部发〔2024〕3号",
    "国发〔2019〕21号",
    "国办发〔2020〕15号",
])
def test_extracts_common_forms(raw):
    assert extract_first(raw) == normalize(raw)


@pytest.mark.parametrize("raw", [
    "税总函〔2024〕5号",
    "税总发〔2023〕18号",
    "税总办发〔2022〕60号",
])
def test_full_width_brackets_after_税总(raw):
    """Regression: the old pattern only allowed ASCII [] here, so every
    税总函／税总发 document with official 〔〕 brackets failed to match."""
    assert extract_first(raw) == normalize(raw)


@pytest.mark.parametrize("raw", [
    "中华人民共和国主席令第7号",
    "主席令第80号",
])
def test_presidential_order(raw):
    """法律 are promulgated by 主席令 — without this, every statute in the
    corpus was unidentifiable."""
    assert extract_first(raw) == normalize(raw)


@pytest.mark.parametrize("raw", [
    "中华人民共和国国务院令第709号",
    "国务院令第691号",
    "国家税务总局令第44号",
])
def test_ministerial_and_state_council_orders(raw):
    assert extract_first(raw) == normalize(raw)


def test_joint_issuance_captured_whole():
    """Scanning must start at the first department, not the first one that
    happens to head a pattern alternative."""
    raw = "海关总署 税务总局公告2025年第256号"
    assert extract_first(raw) == "海关总署税务总局公告2025年第256号"


def test_joint_issuance_with_development_commission():
    raw = "国家税务总局 国家发展改革委 生态环境部公告2021年第11号"
    assert extract_first(raw) == "国家税务总局国家发展改革委生态环境部公告2021年第11号"


def test_normalize_folds_bracket_styles():
    variants = ["财税〔2026〕15号", "财税[2026]15号", "财税（2026）15号", "财税 〔2026〕 15 号"]
    assert len({normalize(v) for v in variants}) == 1


def test_normalize_strips_bom():
    assert normalize("﻿国税发〔2003〕67号") == "国税发〔2003〕67号"


def test_extract_first_handles_bom_prefix():
    assert extract_first("﻿国税发〔2003〕67号") == "国税发〔2003〕67号"


def test_extract_first_returns_none_when_absent():
    assert extract_first("关于企业所得税若干问题的通知") is None
    assert extract_first("") is None


def test_extract_all_finds_every_citation_in_order():
    text = (
        "根据财税〔2026〕15号和国家税务总局公告2025年第8号的规定，"
        "废止国税发〔2003〕67号。"
    )
    assert extract_all(text) == [
        "财税〔2026〕15号",
        "国家税务总局公告2025年第8号",
        "国税发〔2003〕67号",
    ]


def test_extract_all_deduplicates():
    text = "财税〔2026〕15号规定……详见财税〔2026〕15号第二条。"
    assert extract_all(text) == ["财税〔2026〕15号"]


def test_extract_all_empty_input():
    assert extract_all("") == []
    assert extract_all("没有任何文号的一段话") == []


def test_no_false_positive_on_plain_numbers():
    """Bare article/amount references must not be mistaken for 文號."""
    assert extract_all("金额为2026元，共15项") == []
    assert extract_all("本条自第3款起适用") == []
