"""Tests for citation extraction."""

from taxwatch.graph.citation import extract_citations


def test_extract_law_article():
    text = "依據所得稅法第14條第1項第5類規定，個人出租財產之收入應計入所得。"
    citations = extract_citations(text)
    keys = {c.entity_key for c in citations}
    assert "所得稅法#14" in keys or "所得稅法#14#1" in keys


def test_extract_ruling_number():
    text = "本案依台財稅字第10904512340號函釋辦理。"
    citations = extract_citations(text)
    keys = {c.entity_key for c in citations}
    assert "台財稅字第10904512340號" in keys


def test_extract_interpretation():
    text = "釋字第745號闡明租賃所得應扣除必要費用。"
    citations = extract_citations(text)
    keys = {c.entity_key for c in citations}
    assert "釋字第745號" in keys


def test_extract_supersedes():
    text = "台財稅字第10804012340號函釋業經停止適用。"
    citations = extract_citations(text)
    supersedes = [c for c in citations if c.relation_type == "supersedes"]
    assert len(supersedes) >= 1


def test_extract_authority():
    text = "依所得稅法第88條規定，扣繳義務人應辦理扣繳。"
    citations = extract_citations(text)
    authority = [c for c in citations if c.relation_type == "authority_of"]
    assert len(authority) >= 1


def test_no_duplicates():
    text = "所得稅法第14條規定如此，另依所得稅法第14條之解釋。"
    citations = extract_citations(text)
    [c.entity_key for c in citations]
    unique_keys_per_relation = set()
    for c in citations:
        unique_keys_per_relation.add(f"{c.entity_key}:{c.relation_type}")
    assert len(unique_keys_per_relation) == len(citations)


def test_cn_law_article_citation():
    text = "依据企业所得税法第28条规定，小型微利企业减按20%征收。"
    citations = extract_citations(text)
    keys = {c.entity_key for c in citations}
    assert "企业所得税法#28" in keys


def test_cn_wenhao_citation():
    text = "根据财税〔2026〕15号文件，制造业企业可享受加计扣除。"
    citations = extract_citations(text)
    keys = {c.entity_key for c in citations}
    assert any("财税〔2026〕15号" in k for k in keys)


def test_cn_gonggao_citation():
    text = "国家税务总局公告2026年第5号明确了小微企业所得税优惠。"
    citations = extract_citations(text)
    keys = {c.entity_key for c in citations}
    assert any("公告2026年第5号" in k for k in keys)


def test_cn_supersedes():
    text = "废止国税发〔2024〕18号文件。"
    citations = extract_citations(text)
    supersedes = [c for c in citations if c.relation_type == "supersedes"]
    assert len(supersedes) >= 1


def test_cn_authority():
    text = "依据增值税暂行条例第15条的规定免征增值税。"
    citations = extract_citations(text)
    authority = [c for c in citations if c.relation_type == "authority_of"]
    assert len(authority) >= 1


def test_cn_parent_child_law():
    text = "根据《企业所得税法》及其《企业所得税法实施条例》的有关规定。"
    citations = extract_citations(text)
    keys = {c.entity_key for c in citations}
    assert any("实施条例" in k for k in keys)


def test_multiple_citations_in_one_text():
    text = """
    依據所得稅法第14條第1項第5類規定，個人出租財產所取得之租金收入，
    應以全年租賃收入減除必要損耗及費用後之餘額為所得額。
    釋字第745號亦闡明租賃所得應扣除必要費用之意旨。
    本案納稅義務人依台財稅字第10804012340號函釋計算其租賃所得。
    """
    citations = extract_citations(text)
    assert len(citations) >= 3
