"""Tests for text normalization."""
from taxwatch.normalize.text import normalize_text


def test_fullwidth_to_halfwidth():
    assert "123ABC" in normalize_text("１２３ＡＢＣ")


def test_whitespace_collapse():
    result = normalize_text("a  b   c")
    assert result == "a b c"


def test_dash_normalization():
    result = normalize_text("第14—1條")
    assert "-" in result


def test_unicode_normalization():
    r1 = normalize_text("第１４條")
    r2 = normalize_text("第14條")
    assert r1 == r2


def test_cosmetic_diff_prevention():
    old = normalize_text("個人之綜合所得總額，  以其全年各類所得  合併計算之。")
    new = normalize_text("個人之綜合所得總額， 以其全年各類所得 合併計算之。")
    assert old == new
