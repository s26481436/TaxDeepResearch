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
    DocType,
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

    # Priority rank for doc_type: prefer STATUTE over REGULATION, etc.
    type_priority = {
        DocType.STATUTE: 1,
        DocType.REGULATION: 2,
        DocType.ANNOUNCEMENT: 3,
        DocType.RULING: 4,
        DocType.INTERPRETATION: 5,
        DocType.NEWS: 6,
    }

    # Deduplicate documents based on their consolidated root document
    # so we don't extract the same law family tree multiple times.
    roots: dict[str, tuple[Document, str]] = {}  # root_external_id -> (chosen_doc, root_title)
    skipped_duplicates: list[dict[str, str]] = []

    for doc in docs:
        try:
            view = get_consolidated(session, doc.external_id)
            root_ext_id = view.get("external_id") or doc.external_id
            root_title = view.get("title") or doc.title
        except Exception:
            root_ext_id = doc.external_id
            root_title = doc.title

        if root_ext_id not in roots:
            roots[root_ext_id] = (doc, root_title)
        else:
            existing_doc, existing_root_title = roots[root_ext_id]
            # Choose document with higher priority (or stable sort on external_id)
            p_existing = type_priority.get(existing_doc.doc_type, 99)
            p_new = type_priority.get(doc.doc_type, 99)
            if (p_new, doc.external_id) < (p_existing, existing_doc.external_id):
                skipped_duplicates.append(
                    {
                        "title": existing_doc.title,
                        "external_id": existing_doc.external_id,
                        "root_title": existing_root_title,
                    }
                )
                roots[root_ext_id] = (doc, root_title)
            else:
                skipped_duplicates.append(
                    {
                        "title": doc.title,
                        "external_id": doc.external_id,
                        "root_title": root_title,
                    }
                )

    deduped_docs = [chosen_doc for chosen_doc, _ in roots.values()]

    # If multiple statutes/regulations exist, process root/statutes or each doc
    overall_stats: dict[str, Any] = {
        "tax_key": tax_key,
        "country": resolved_country,
        "tax_name": tax_type.name_zh,
        "documents_processed": len(deduped_docs),
        "source_documents": [d.title for d in deduped_docs],
        "skipped_duplicates": skipped_duplicates,
        "requirements": 0,
        "dropped_citations": 0,
        "uncited_fields": 0,
        "results": [],
    }

    for doc in deduped_docs:
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

    if not dry_run:
        suspected = detect_suspected_duplicates(session, resolved_country, tax_key)
        overall_stats["suspected_duplicates"] = suspected
    else:
        overall_stats["suspected_duplicates"] = []

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

    batches = _render_batches(view)
    all_allowed_nodes: set[str] = set().union(*(b[1] for b in batches)) if batches else set()
    if not all_allowed_nodes:
        raise NoSourceDocument(f"{external_id} has no parsed provisions to extract from")

    client = get_llm_client()
    field_defs = format_field_definitions()

    all_requirements: list[Any] = []
    all_unresolved: list[str] = []
    known_scenarios: set[tuple[str, str]] = set()

    for batch_idx, (provisions_block, batch_allowed) in enumerate(batches, start=1):
        if known_scenarios:
            scenarios_list = "\n".join(
                f"- 情境：{sc} | 納稅人身分：{role}" for sc, role in known_scenarios if sc
            )
            existing_section = (
                f"\n## 前面批次已識別之情境清單\n\n"
                f"以下是前面批次已整理出的情境與身分。若本批條文所屬情境已包含在下列清單中，"
                f"**必須逐字沿用該情境的 scenario 與 taxpayer_role 措辭**，不要另創新說法；"
                f"只有確定為全新情境時才新增：\n\n"
                f"{scenarios_list}\n"
            )
        else:
            existing_section = ""

        prompt = EXTRACTION_TEMPLATE.format(
            tax_name=tax_name,
            existing_scenarios_section=existing_section,
            field_definitions=field_defs,
            provisions=provisions_block,
        )

        result = client.generate_structured(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
            output_model=RequirementSetOut,
        )
        all_requirements.extend(result.requirements)
        all_unresolved.extend(result.unresolved)
        for r in result.requirements:
            scenario_str = (r.scenario or "").strip()
            role_str = (r.taxpayer_role or "").strip()
            if scenario_str:
                known_scenarios.add((scenario_str, role_str))

    child_titles = [c["title"] for c in view.get("child_documents", [])]
    stats: dict[str, Any] = {
        "tax_key": resolved_tax_key,
        "source_document": view["title"],
        "child_documents": child_titles,
        "missing_parent": missing_parent,
        "batches": len(batches),
        "provisions_supplied": len(all_allowed_nodes),
        "requirements": len(all_requirements),
        "unresolved": all_unresolved,
        "truncated_nodes": 0,
        "dropped_citations": 0,
        "uncited_fields": 0,
    }

    if dry_run:
        stats["preview"] = [
            {"scenario": r.scenario, "taxpayer_role": r.taxpayer_role} for r in all_requirements
        ]
        return stats

    for row in all_requirements:
        dropped, uncited = _upsert_requirement(
            session,
            row,
            country=resolved_country,
            tax_key=resolved_tax_key,
            document=document,
            allowed_nodes=all_allowed_nodes,
            model=client.model,
        )
        stats["dropped_citations"] += dropped
        stats["uncited_fields"] += uncited

    session.commit()
    logger.info(
        "Extracted %d requirement rows across %d batches for %s (%d citations dropped as unverifiable)",
        stats["requirements"],
        stats["batches"],
        resolved_tax_key,
        stats["dropped_citations"],
    )
    return stats


# ---------- internals ----------


def _render_batches(view: dict[str, Any]) -> list[tuple[str, set[str]]]:
    """Split consolidated view provisions into batches that fit within _MAX_PROVISION_CHARS.

    Splits occur on article/supplement boundaries.
    """
    # 1. Build a list of self-contained provision blocks: (block_text, node_keys_in_block)
    blocks: list[tuple[str, set[str]]] = []

    for article in view["articles"]:
        art_lines = [f"\n### [{article['node_key']}] {article['heading']}\n{article['text']}"]
        art_nodes = {article["node_key"]}

        for supplement in article.get("supplements", []):
            art_lines.append(
                f"\n  補充規定 [{supplement['node_key']}] "
                f"《{supplement['document_title']}》{supplement['heading']}\n"
                f"  {supplement['text']}"
            )
            art_nodes.add(supplement["node_key"])

        blocks.append(("\n".join(art_lines), art_nodes))

    for supplement in view.get("unanchored_supplements", []):
        block = (
            f"\n### [{supplement['node_key']}] "
            f"《{supplement['document_title']}》{supplement['heading']}\n{supplement['text']}"
        )
        blocks.append((block, {supplement["node_key"]}))

    if not blocks:
        return []

    batches: list[tuple[str, set[str]]] = []
    current_lines: list[str] = []
    current_nodes: set[str] = set()
    current_chars = 0

    for block_text, block_nodes in blocks:
        block_len = len(block_text)
        # If adding this block exceeds budget (and current batch is not empty), finish current batch
        if current_chars + block_len > _MAX_PROVISION_CHARS and current_lines:
            batches.append(("\n".join(current_lines), current_nodes))
            current_lines = []
            current_nodes = set()
            current_chars = 0

        current_lines.append(block_text)
        current_nodes.update(block_nodes)
        current_chars += block_len

    if current_lines:
        batches.append(("\n".join(current_lines), current_nodes))

    return batches


def _render_provisions(view: dict[str, Any]) -> tuple[str, set[str], int]:
    """Lay out the consolidated view for the prompt, collecting valid node keys."""
    batches = _render_batches(view)
    if not batches:
        return "", set(), 0
    return batches[0][0], batches[0][1], 0


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

        # Across multiple batches: do not overwrite a previously cited, verified cell with an uncited one
        if current is not None and current.citations and not citations:
            continue

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


def normalize_rate_for_comparison(rate: str) -> str:
    """Normalize a rate/levy string for deduplication reporting without losing meaning.

    Handles whitespace, full-width characters, and Chinese percentage forms
    like 百分之三 -> 3%.
    """
    import re
    from taxwatch.cn_numerals import to_arabic

    text = rate.strip()
    # Normalize full-width ascii & punctuation
    text = text.replace("％", "%").replace("（", "(").replace("）", ")").replace("：", ":")
    text = re.sub(r"\s+", "", text)

    # Convert 百分之X -> X%
    def _repl_pct(m: re.Match) -> str:
        num_str = m.group(1)
        arabic = to_arabic(num_str)
        return f"{arabic}%"

    text = re.sub(r"百分之([零〇一二两兩三四五六七八九十百]+(?:\.[0-9]+)?)", _repl_pct, text)
    return text


def detect_suspected_duplicates(
    session: Session,
    country: str,
    tax_key: str,
) -> list[dict[str, Any]]:
    """Detect suspected duplicate requirement rows based on identical taxpayer_role and normalized rate.

    Reporting only — does NOT automatically merge or delete rows.
    """
    reqs = (
        session.query(TaxRequirement)
        .filter_by(country=country, tax_key=tax_key)
        .order_by(TaxRequirement.id.asc())
        .all()
    )

    # Group by (taxpayer_role, normalized_rate)
    grouped: dict[tuple[str, str], list[TaxRequirement]] = {}
    for r in reqs:
        fields = {f.field_key: f for f in r.fields}
        rate_val = fields["rate"].value if "rate" in fields else ""
        norm_rate = normalize_rate_for_comparison(rate_val)
        role = r.taxpayer_role.strip()
        grouped.setdefault((role, norm_rate), []).append(r)

    suspected: list[dict[str, Any]] = []
    for (role, norm_rate), group in grouped.items():
        if len(group) > 1:
            suspected.append(
                {
                    "taxpayer_role": role,
                    "normalized_rate": norm_rate,
                    "count": len(group),
                    "scenarios": [r.scenario for r in group],
                    "requirement_ids": [r.id for r in group],
                }
            )

    return suspected


def _infer_tax_key(title: str, country: str = "CN") -> str:
    return classify(title, country=country).key


def _tax_name(tax_key: str) -> str:
    tax_type = by_key(tax_key)
    return tax_type.name_zh if tax_type else UNCLASSIFIED.name_zh
