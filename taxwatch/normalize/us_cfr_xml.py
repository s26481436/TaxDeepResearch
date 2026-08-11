"""Normalizer for CFR XML (govinfo.gov bulk format and eCFR XML).

Both sources emit the same SGML-derived XML vocabulary:
  <SECTION>
    <SECTNO>§ 1.1-1</SECTNO>
    <SUBJECT>Income tax on individuals.</SUBJECT>
    <P>General rule. ...</P>
    <P>...</P>
  </SECTION>

Higher structure elements (PART, SUBPART, SECTION) are traversed recursively.
Chapter headings (SUBPART/HD) are tracked as context but not emitted as
provisions — matching the same convention as the TW normalizer.

Node key format:  "26 CFR § 1.1-1"  (CFR citation style)
"""

from __future__ import annotations

import re
from xml.etree import ElementTree as ET

from taxwatch.connectors.base import RawDocument
from taxwatch.normalize.base import NormalizedDoc, Normalizer, ProvisionData
from taxwatch.normalize.text import normalize_text

# Namespace-agnostic tag helper
_TAG_RE = re.compile(r"(?:\{[^}]+\})?(\w+)")


def _tag(el: ET.Element) -> str:
    m = _TAG_RE.match(el.tag)
    return m.group(1) if m else el.tag


def _text(el: ET.Element) -> str:
    """Extract all text content from element tree, stripping tags."""
    return normalize_text("".join(el.itertext()))


class UsCfrXmlNormalizer(Normalizer):
    def normalize(self, raw: RawDocument) -> NormalizedDoc:
        content = raw.content if isinstance(raw.content, bytes) else raw.content.encode()
        root = ET.fromstring(content)

        provisions: list[ProvisionData] = []
        _collect_sections(root, provisions)

        # Build title from TITLE element or external_id
        title_el = root.find(".//{*}CFRTITLE") or root.find(".//{*}TITLENUM")
        title = _text(title_el).strip() if title_el is not None else raw.external_id

        # Pull part number from metadata or external_id
        part = raw.metadata.get("part", "")
        if part:
            title = f"26 CFR Part {part}"

        return NormalizedDoc(
            external_id=raw.external_id,
            title=title,
            provisions=provisions,
            metadata={
                "source_format": "us_cfr_xml",
                "cfr_title": raw.metadata.get("cfr_title", "26"),
                "part": part,
                "jurisdiction": raw.metadata.get("jurisdiction", "US-federal"),
                "volume": raw.metadata.get("volume", ""),
            },
        )


def _collect_sections(el: ET.Element, out: list[ProvisionData]) -> None:
    t = _tag(el)
    if t == "SECTION":
        _parse_section(el, out)
        return
    for child in el:
        _collect_sections(child, out)


def _parse_section(el: ET.Element, out: list[ProvisionData]) -> None:
    sectno_el = el.find("{*}SECTNO") or el.find("SECTNO")
    subject_el = el.find("{*}SUBJECT") or el.find("SUBJECT")

    if sectno_el is None:
        return

    sectno = _text(sectno_el).strip()  # e.g. "§ 1.1-1"
    subject = _text(subject_el).strip() if subject_el is not None else ""

    # Gather body paragraphs
    paras: list[str] = []
    for child in el:
        ctag = _tag(child)
        if ctag in ("SECTNO", "SUBJECT"):
            continue
        text = _text(child).strip()
        if text:
            paras.append(text)

    body = "\n".join(paras)
    if not body and not subject:
        return

    node_key = _build_node_key(sectno)
    heading = f"{sectno}  {subject}".strip()
    out.append(
        ProvisionData(
            node_key=node_key,
            heading=heading,
            text=normalize_text(f"{subject}\n{body}".strip()),
        )
    )


def _build_node_key(sectno: str) -> str:
    """'§ 1.1-1' → '26 CFR § 1.1-1'"""
    cleaned = re.sub(r"\s+", " ", sectno).strip()
    if cleaned.startswith("§"):
        return f"26 CFR {cleaned}"
    return f"26 CFR § {cleaned}"
