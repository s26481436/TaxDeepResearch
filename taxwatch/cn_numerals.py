"""Chinese numeral conversion — one source of truth for 條號.

Both the normalizer (splitting 第一條/第一条) and citation extraction (matching
references to them) must agree on how 第一百三十三条 becomes ``133``, or the
node keys they produce never line up and the legal graph stays empty.

Handles the forms that actually appear in article numbering — up to 千, with
the colloquial leading-十 (十一 = 11) and 兩 = 2 — and returns ``None`` for
anything else rather than guessing.
"""

from __future__ import annotations

_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "兩": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

_UNITS = {"十": 10, "百": 100, "千": 1000}

CN_NUMERAL_CHARS = "".join(_DIGITS) + "".join(_UNITS)


def to_int(text: str) -> int | None:
    """Convert a Chinese numeral to an int, or None if it isn't one.

    >>> to_int("一百三十三")
    133
    >>> to_int("十一")
    11
    """
    text = text.strip()
    if not text:
        return None

    total = 0
    pending = 0
    saw_digit = False

    for ch in text:
        if ch in _DIGITS:
            pending = _DIGITS[ch]
            saw_digit = True
        elif ch in _UNITS:
            # A bare leading unit means one of it: 十一 is 11, not 1.
            total += (pending or 1) * _UNITS[ch]
            pending = 0
            saw_digit = True
        else:
            return None

    return total + pending if saw_digit else None


def to_arabic(text: str) -> str:
    """Convert a Chinese numeral to its Arabic string, or return it unchanged.

    Callers use the result as part of a node key, so an unconvertible input is
    passed through rather than dropped — a slightly odd key still beats losing
    the provision.
    """
    value = to_int(text)
    return str(value) if value is not None else text
