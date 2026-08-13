"""Tests for normalizers."""

from pathlib import Path

from taxwatch.connectors.base import RawDocument
from taxwatch.normalize.cn_tax_html import CnTaxHtmlNormalizer
from taxwatch.normalize.tw_law_json import TwLawJsonNormalizer
from taxwatch.normalize.tw_ruling_html import TwRulingHtmlNormalizer

FIXTURES = Path(__file__).parent / "fixtures"


def test_tw_law_json_normalizer():
    raw_content = (FIXTURES / "tw_law_sample.json").read_bytes()
    raw = RawDocument(external_id="G0340001", content=raw_content, content_type="application/json")

    normalizer = TwLawJsonNormalizer()
    result = normalizer.normalize(raw)

    assert result.title == "所得稅法"
    assert len(result.provisions) == 3

    keys = [p.node_key for p in result.provisions]
    assert "所得稅法#1" in keys
    assert "所得稅法#2" in keys
    assert "所得稅法#14" in keys

    art14 = next(p for p in result.provisions if p.node_key == "所得稅法#14")
    assert "營利所得" in art14.text
    assert "薪資所得" in art14.text


def test_tw_ruling_html_normalizer():
    raw_content = (FIXTURES / "tw_ruling_sample.html").read_bytes()
    raw = RawDocument(
        external_id="台財稅字第10904512340號",
        content=raw_content,
        content_type="text/html",
    )

    normalizer = TwRulingHtmlNormalizer()
    result = normalizer.normalize(raw)

    assert "台財稅字第10904512340號" in result.title or len(result.provisions) > 0

    all_text = " ".join(p.text for p in result.provisions)
    assert "所得稅法" in all_text
    assert "租賃所得" in all_text


def test_cn_tax_html_normalizer_law():
    raw_content = (FIXTURES / "cn_tax_sample.html").read_bytes()
    raw = RawDocument(
        external_id="cn-enterprise-income-tax-law",
        content=raw_content,
        content_type="text/html; charset=utf-8",
    )

    normalizer = CnTaxHtmlNormalizer()
    result = normalizer.normalize(raw)

    assert "企业所得税法" in result.title
    assert len(result.provisions) >= 3
    assert result.metadata["hierarchy_level"] == "law"

    keys = [p.node_key for p in result.provisions]
    assert any("#1" in k for k in keys)
    assert any("#28" in k for k in keys)


def test_cn_tax_html_normalizer_notice():
    raw_content = (FIXTURES / "cn_notice_sample.html").read_bytes()
    raw = RawDocument(
        external_id="caishuigonggao-2026-3",
        content=raw_content,
        content_type="text/html; charset=utf-8",
    )

    normalizer = CnTaxHtmlNormalizer()
    result = normalizer.normalize(raw)

    assert len(result.provisions) >= 3
    assert result.metadata["hierarchy_level"] == "notice"

    all_text = " ".join(p.text for p in result.provisions)
    assert "小型微利企业" in all_text
    assert "研发费用" in all_text


def test_cn_tax_html_strips_fgk_boilerplate():
    """fgk pages embed UI widgets inside the .content container."""
    html = """<html><body><div class="content">
    <h2>中华人民共和国增值税法</h2>
    <p>下载文字版</p>
    <p>下载图片版</p>
    <p>字体: 【大】 【中】 【小】</p>
    <p>分享到: 收藏 订阅</p>
    <p>已推送,请在 "个人中心-我的订阅" 中查看</p>
    <p>此稿件无标签,进入 "订阅设置" 中订阅更多</p>
    <p>全文有效</p>
    <p>语音播报:</p>
    <p>扫一扫在手机打开当前页</p>
    <p>第一条 在中华人民共和国境内销售货物的单位和个人，为增值税的纳税人。</p>
    <p>第二条 增值税税率：（一）纳税人销售货物，税率为百分之十三。</p>
    <p>第三条 纳税人兼营不同税率的项目，应当分别核算。</p>
    <p>【打印】 【下载】</p>
    <p>纠错或建议</p>
    <p>历史沿革</p>
    <p>关联解读</p>
    <p>关联文件</p>
    <p>关于《国家税务总局关于简并税费申报有关事项的公告》的解读</p>
    <p>关联问答</p>
    </div></body></html>"""

    raw = RawDocument(
        external_id="vat-law",
        content=html.encode(),
        content_type="text/html; charset=utf-8",
        metadata={"title": "中华人民共和国增值税法"},
    )
    normalizer = CnTaxHtmlNormalizer()
    result = normalizer.normalize(raw)

    all_text = " ".join(p.text for p in result.provisions)
    assert "纳税人" in all_text
    assert "下载文字版" not in all_text
    assert "字体" not in all_text
    assert "分享到" not in all_text
    assert "扫一扫" not in all_text
    assert "纠错或建议" not in all_text
    assert "关联解读" not in all_text
    assert len(result.provisions) == 3
