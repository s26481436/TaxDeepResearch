"""Text normalization to reduce false-positive diffs from formatting noise."""
from __future__ import annotations

import re
import unicodedata


def normalize_text(text: str) -> str:
    text = _normalize_unicode(text)
    text = _normalize_whitespace(text)
    text = _normalize_punctuation(text)
    return text.strip()


def _normalize_unicode(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    _FULLWIDTH_MAP = str.maketrans(
        "０１２３４５６７８９"
        "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
        "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
        "0123456789"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        "abcdefghijklmnopqrstuvwxyz",
    )
    text = text.translate(_FULLWIDTH_MAP)
    return text


def _normalize_whitespace(text: str) -> str:
    text = re.sub(r"[          　]", " ", text)
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _normalize_punctuation(text: str) -> str:
    replacements = {
        "‘": "'", "’": "'",
        "“": '"', "”": '"',
        "‐": "-", "‑": "-", "‒": "-",
        "–": "-", "—": "-", "―": "-",
        "，": "，",
        "．": ".",
        "：": "：",
        "；": "；",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text
