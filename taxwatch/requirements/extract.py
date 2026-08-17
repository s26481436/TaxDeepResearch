"""Derive 申報規範 rows from the law itself.

The input is the consolidated view — a statute's articles each followed by the
implementing provisions that expand them — because that is what the rule
actually is. Extracting from the statute alone would produce a rate with no
scope and a deadline with no procedure, since the operative detail lives in the
实施条例 and the 公告 issued under it.

Every cell the model returns is checked back against the input before it is
stored: a citation naming a node key that was never shown is dropped, and a
cell left with no surviving citation is recorded at zero confidence and flagged
for review. The model can be wrong about what a provision means; it should not
be able to invent which provisions exist.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from taxwatch.analysis.client import get_llm_client
from taxwatch.models import (
    Document,
    FieldSource,
    RequirementField,
    RequirementStatus,
    TaxRequirement,
)
from taxwatch.requirements.fields import FIELD_KEYS
from taxwatch.requirements.prompts import (
    EXTRACTION_TEMPLATE,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    format_field_definitions,
)
from taxwatch.requirements.schema import RequirementSetOut
from taxwatch.services.consolidated import get_consolidated
from taxwatch.taxonomy import UNCLASSIFIED, by_key, classify

logger = logging.getLogger(__name__)

# Guard against handing the model a whole tax code; the statutes we care about
# run to a few hundred articles, and beyond that the useful signal is already in.
_MAX_PROVISION_CHARS = 60_000


class NoSourceDocument(LookupError):
    """Raised when no monitored document can act as the basis for a tax type."""


class CountryMismatch(ValueError):
    """Raised when an explicit country argument conflicts with the document's source country."""

    def __init__(self, expected: str, actual: str):
        super().__init__(f"來源轄區為 {actual}，但指定了 {expected}")
        self.expected = expected
        self.actual = actual


class MissingParentLaw(LookupError):
    """Raised when only the 子法 is on file and the 母法 it implements is not.

    An 实施条例 read alone is not the rule. It defines terms the statute
    introduces — 销售货物, 应税交易 — without ever stating who owes the tax, on
    what, or when; those are the statute's articles. Extracting 申報規範 from it
    would produce rows whose 課稅情境 nothing in the supplied text establishes,
    which is exactly the material this pipeline refuses to invent.
    """

    def __init__(self, child_title: str, parent_key: str, status: str):
        super().__init__(parent_key)
        self.child_title = child_title
        self.parent_key = parent_key
        self.status = status


def extract_for_tax(
    session: Session,
    tax_key: str,
    *,
    country: str | None = None,
    dry_run: bool = False,
    allow_child: bool = False,
) -> dict[str, Any]:
    """Extract 申報規範 for all statutes/regulations belonging to a specific tax_key.

    If country is not provided, it is resolved from the tax_type definition.
    """
    tax_type = by_key(tax_key)
    if tax_type is None:
        raise LookupError(f"未知稅種代碼: {tax_key}")

    resolved_country = (country or tax_type.country).upper()
    if country is not None and country.upper() != tax_type.country.upper():
        raise CountryMismatch(expected=country.upper(), actual=tax_type.country.upper())

    from taxwatch.services.documents import list_statutes_for_tax

    docs = list_statutes_for_tax(session, resolved_country, tax_key)
    if not docs:
        raise NoSourceDocument(f"未找到屬於 {resolved_country} / {tax_key} 的法規文檔")

    # If multiple statutes/regulations exist, process root/statutes or each doc
    overall_stats: dict[str, Any] = {
        "tax_key": tax_key,
        "country": resolved_country,
        "tax_name": tax_type.name_zh,
        "documents_processed": len(docs),
        "source_documents": [d.title for d in docs],
        "requirements": 0,
        "dropped_citations": 0,
        "uncited_fields": 0,
        "results": [],
    }

    for doc in docs:
        try:
            stat = extract_for_document(
                session,
                doc.external_id,
                country=resolved_country,
                tax_key=tax_key,
                dry_run=dry_run,
                allow_child=allow_child,
            )
            overall_stats["results"].append(stat)
            overall_stats["requirements"] += stat.get("requirements", 0)
            overall_stats["dropped_citations"] += stat.get("dropped_citations", 0)
            overall_stats["uncited_fields"] += stat.get("uncited_fields", 0)
        except MissingParentLaw:
            if not allow_child:
                raise
        except NoSourceDocument:
            continue

    return overall_stats


def extract_for_document(
    session: Session,
    external_id: str,
    *,
    country: str | None = None,
    tax_key: str | None = None,
    dry_run: bool = False,
    allow_child: bool = False,
) -> dict[str, Any]:
    """Extract 申報規範 for one statute and everything implementing it.

    Refuses to run against an orphaned 子法 unless `allow_child` is set: the
    caller asked for a tax's reporting rules, and a regulation without its
    statute cannot supply them.
    """
    view = get_consolidated(session, external_id)
    missing_parent = view.get("missing_parent")
    if missing_parent and not allow_child:
        raise MissingParentLaw(
            view["title"],
            missing_parent["key"],
            missing_parent["status"],
        )

    document = session.query(Document).filter_by(external_id=view["external_id"]).first()
    derived_country = document.source.country if (document and document.source) else "CN"

    if country is not None and country.strip() != "":
        if country.upper() != derived_country.upper():
            raise CountryMismatch(expected=country.upper(), actual=derived_country.upper())
        resolved_country = country.upper()
    else:
        resolved_country = derived_country.upper()

    resolved_tax_key = tax_key or _infer_tax_key(view["title"], country=resolved_country)
    tax_name = _tax_name(resolved_tax_key)

    provisions_block, allowed_nodes, truncated_nodes = _render_provisions(view)
    if not allowed_nodes:
        raise NoSourceDocument(f"{external_id} has no parsed provisions to extract from")

    prompt = EXTRACTION_TEMPLATE.format(
        tax_name=tax_name,
        field_definitions=format_field_definitions(),
        provisions=provisions_block,
    )

    client = get_llm_client()
    result = client.generate_structured(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=prompt,
        output_model=RequirementSetOut,
    )

    child_titles = [c["title"] for c in view.get("child_documents", [])]
    stats: dict[str, Any] = {
        "tax_key": resolved_tax_key,
        "source_document": view["title"],
        "child_documents": child_titles,
        "missing_parent": missing_parent,
        "provisions_supplied": len(allowed_nodes),
        "requirements": len(result.requirements),
        "unresolved": result.unresolved,
        "truncated_nodes": truncated_nodes,
        "dropped_citations": 0,
        "uncited_fields": 0,
    }

    if dry_run:
        stats["preview"] = [
            {"scenario": r.scenario, "taxpayer_role": r.taxpayer_role} for r in result.requirements
        ]
        return stats

    for row in result.requirements:
        dropped, uncited = _upsert_requirement(
            session,
            row,
            country=resolved_country,
            tax_key=resolved_tax_key,
            document=document,
            allowed_nodes=allowed_nodes,
            model=client.model,
        )
        stats["dropped_citations"] += dropped
        stats["uncited_fields"] += uncited

    session.commit()
    logger.info(
        "Extracted %d requirement rows for %s (%d citations dropped as unverifiable)",
        stats["requirements"],
        resolved_tax_key,
        stats["dropped_citations"],
    )
    return stats


# ---------- internals ----------


def _render_provisions(view: dict[str, Any]) -> tuple[str, set[str], int]:
    """Lay out the consolidated view for the prompt, collecting valid node keys."""
    lines: list[str] = []
    allowed: set[str] = set()
    budget = _MAX_PROVISION_CHARS

    truncated = 0

    def emit(text: str) -> bool:
        nonlocal budget, truncated
        if budget - len(text) < 0:
            truncated += 1
            return False
        lines.append(text)
        budget -= len(text)
        return True

    for article in view["articles"]:
        block = f"\n### [{article['node_key']}] {article['heading']}\n{article['text']}"
        if not emit(block):
            break
        allowed.add(article["node_key"])

        for supplement in article["supplements"]:
            ok = emit(
                f"\n  補充規定 [{supplement['node_key']}] "
                f"《{supplement['document_title']}》{supplement['heading']}\n"
                f"  {supplement['text']}"
            )
            if ok:
                allowed.add(supplement["node_key"])

    for supplement in view.get("unanchored_supplements", []):
        ok = emit(
            f"\n### [{supplement['node_key']}] "
            f"《{supplement['document_title']}》{supplement['heading']}\n{supplement['text']}"
        )
        if ok:
            allowed.add(supplement["node_key"])

    return "\n".join(lines), allowed, truncated


def _upsert_requirement(
    session: Session,
    row: Any,
    *,
    country: str,
    tax_key: str,
    document: Document | None,
    allowed_nodes: set[str],
    model: str,
) -> tuple[int, int]:
    scenario = (row.scenario or "").strip() or "未分類情境"
    taxpayer_role = (row.taxpayer_role or "").strip()

    requirement = (
        session.query(TaxRequirement)
        .filter_by(
            country=country,
            tax_key=tax_key,
            scenario=scenario,
            taxpayer_role=taxpayer_role,
        )
        .first()
    )
    if requirement is None:
        requirement = TaxRequirement(
            country=country,
            tax_key=tax_key,
            scenario=scenario,
            taxpayer_role=taxpayer_role,
        )
        session.add(requirement)

    requirement.status = RequirementStatus.DRAFT
    requirement.model = model
    requirement.prompt_version = PROMPT_VERSION
    if document is not None:
        requirement.source_document_id = document.id
    session.flush()

    existing = {f.field_key: f for f in requirement.fields}
    dropped = 0
    uncited = 0
    written: set[str] = set()

    for field_out in row.fields:
        if field_out.field_key not in FIELD_KEYS:
            logger.warning("Ignoring unknown field key from model: %s", field_out.field_key)
            continue

        # A malformed response can name the same column twice. Take the first
        # and drop the rest: letting a later duplicate win would allow a junk
        # repeat to clobber a well-cited answer, and inserting both violates
        # the one-cell-per-column constraint outright.
        if field_out.field_key in written:
            logger.warning(
                "Model returned %s twice for %s; keeping the first",
                field_out.field_key,
                requirement.scenario,
            )
            continue
        written.add(field_out.field_key)

        current = existing.get(field_out.field_key)
        # A human-authored cell is the authority — whether typed in the UI or
        # imported from the finance spreadsheet. Regeneration must not quietly
        # overwrite a person's answer with the model's.
        if current is not None and current.source in (FieldSource.MANUAL, FieldSource.IMPORT):
            continue

        citations, removed = _verify_citations(field_out.citations, allowed_nodes)
        dropped += removed
        confidence = field_out.confidence if citations else 0.0
        if not citations:
            uncited += 1

        if current is None:
            current = RequirementField(
                requirement_id=requirement.id,
                field_key=field_out.field_key,
            )
            session.add(current)
            existing[field_out.field_key] = current

        current.value = field_out.value.strip()
        current.citations = citations
        current.confidence = confidence
        current.source = FieldSource.LLM
        # An uncited cell is the model's own words; make a reviewer look at it.
        current.needs_review = not citations
        current.review_reason = "無條文依據，需人工確認" if not citations else ""
        current.stale_change_id = None

    session.flush()
    return dropped, uncited


def _verify_citations(citations: list[Any], allowed_nodes: set[str]) -> tuple[list[dict], int]:
    """Keep only citations naming a provision that was actually supplied."""
    kept: list[dict] = []
    dropped = 0
    for citation in citations:
        node_key = (citation.node_key or "").strip()
        if node_key not in allowed_nodes:
            dropped += 1
            continue
        kept.append(
            {
                "node_key": node_key,
                "title": (citation.title or "").strip(),
                "quote": (citation.quote or "").strip(),
            }
        )
    return kept, dropped


def _infer_tax_key(title: str, country: str = "CN") -> str:
    return classify(title, country=country).key


def _tax_name(tax_key: str) -> str:
    tax_type = by_key(tax_key)
    return tax_type.name_zh if tax_type else UNCLASSIFIED.name_zh
