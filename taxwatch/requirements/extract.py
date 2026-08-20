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
import time
from typing import Any

from sqlalchemy.orm import Session

from taxwatch.analysis.client import LLMOutputTruncated, get_llm_client
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
#
# This was the single-shot ceiling before extraction was batched. Kept as the
# absolute cap, but the *batch* size is a separate, smaller setting: a 60k-char
# request was measured at 6m41s against the production gateway, which is long
# enough that any transient wobble kills it. Smaller batches finish sooner and
# lean on the gateway less.
_MAX_PROVISION_CHARS = 60_000

# A quote exists to locate the passage inside an article, not to reproduce it.
# The full text is already in the database under the same node_key.
_MAX_QUOTE_CHARS = 30


def _batch_chars() -> int:
    from taxwatch.config import get_settings

    # The hard cap wins: it is the ceiling, and tests lower it to force batching.
    return max(1, min(get_settings().requirements_batch_chars, _MAX_PROVISION_CHARS))


class LLMBatchFailure(RuntimeError):
    """Raised when every batch of an extraction failed.

    A partial result is worth keeping; nothing at all is not, and returning
    empty stats would look like "this statute has no requirements".
    """


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
        # Carried up from each document so --dry-run can show what a run would
        # produce. Without it the tax-level path reports a count and nothing
        # else, which is the one thing a dry run exists to avoid.
        "preview": [],
        "unresolved": [],
        "results": [],
    }

    # Accumulated across documents, not just across batches within one. Each
    # batch otherwise invents its own version of the same scenario, annotated
    # with whatever provisions it happened to see:
    #   營利事業一般所得稅申報（含存貨估價、成本認列…）
    #   營利事業一般所得稅申報（含附贈、分期付款、工程…）
    # and provisions that should have been folded into an existing row become
    # rows of their own, because the model cannot fold into what it cannot see.
    known_scenarios: list[tuple[str, str]] = []

    for doc in docs:
        try:
            stat = extract_for_document(
                session,
                doc.external_id,
                country=resolved_country,
                tax_key=tax_key,
                dry_run=dry_run,
                allow_child=allow_child,
                known_scenarios=known_scenarios,
            )
            overall_stats["results"].append(stat)
            overall_stats["requirements"] += stat.get("requirements", 0)
            overall_stats["dropped_citations"] += stat.get("dropped_citations", 0)
            overall_stats["uncited_fields"] += stat.get("uncited_fields", 0)
            overall_stats["preview"].extend(stat.get("preview", []))
            overall_stats["unresolved"].extend(stat.get("unresolved", []))
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
    known_scenarios: list[tuple[str, str]] | None = None,
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
    all_allowed_nodes: set[str] = set()
    for group in batches:
        all_allowed_nodes.update(_group_nodes(group))
    if not all_allowed_nodes:
        raise NoSourceDocument(f"{external_id} has no parsed provisions to extract from")

    # Everything the prompts need has been read. Close the read transaction
    # before the LLM phase: a batched extraction spends minutes to tens of
    # minutes out there once gateway retries and suspension backoffs are
    # counted, and a connection left open through that is routinely closed by
    # the server or a connection proxy. Committing returns it to the pool, so
    # the writes below check out a fresh, pre-pinged connection instead of a
    # corpse.
    session.commit()

    client = get_llm_client()
    field_defs = format_field_definitions()

    all_requirements: list[Any] = []
    all_unresolved: list[str] = []
    failed_batches: list[dict[str, Any]] = []

    from taxwatch.config import get_settings

    inter_batch_delay = get_settings().llm_inter_batch_delay

    if known_scenarios is None:
        known_scenarios = []

    # Work stack rather than a plain loop: a batch whose output overran the
    # token budget is split in half and both halves pushed back. Asking for
    # more output instead would lengthen the generation, which is precisely
    # what times out on this deployment's gateway — trading a truncation, from
    # which a split recovers, for a 502, from which nothing does.
    pending: list[tuple[str, list[tuple[str, set[str]]]]] = [
        (str(i), group) for i, group in enumerate(reversed(batches), start=1)
    ]
    batch_seq = 0

    while pending:
        label, group = pending.pop()
        batch_seq += 1

        # Concurrent requests are what trips the gateway into 400s; space them.
        if batch_seq > 1 and inter_batch_delay > 0:
            time.sleep(inter_batch_delay)

        prompt = EXTRACTION_TEMPLATE.format(
            tax_name=tax_name,
            known_scenarios_section=_known_scenarios_section(known_scenarios),
            field_definitions=field_defs,
            provisions=_group_text(group),
        )

        try:
            result = client.generate_structured(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=prompt,
                output_model=RequirementSetOut,
            )
        except LLMOutputTruncated as exc:
            if len(group) > 1:
                mid = len(group) // 2
                logger.warning(
                    "Batch %s output overran the token budget; splitting %d blocks into %d + %d",
                    label,
                    len(group),
                    mid,
                    len(group) - mid,
                )
                pending.append((f"{label}b", group[mid:]))
                pending.append((f"{label}a", group[:mid]))
                continue
            # A single provision that cannot be answered within the budget is
            # not divisible any further.
            logger.warning("Batch %s is a single block and still overran; giving up on it", label)
            failed_batches.append(
                {"batch": label, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
            )
            continue
        except Exception as exc:  # noqa: BLE001 — one bad batch must not void the rest
            # Losing batch 9 used to discard batches 1-8 as well, because
            # nothing was written until every batch had returned. A partial
            # matrix that names its own gaps beats an empty one.
            logger.warning(
                "Batch %s failed (%s): %s",
                label,
                type(exc).__name__,
                str(exc)[:200],
            )
            failed_batches.append(
                {"batch": label, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
            )
            continue

        all_requirements.extend(result.requirements)
        all_unresolved.extend(result.unresolved)

        seen = {(s, r) for s, r in known_scenarios}
        for row in result.requirements:
            row.scenario = _strip_echoed_role(row.scenario)
            identity = ((row.scenario or "").strip(), (row.taxpayer_role or "").strip())
            if identity[0] and identity not in seen:
                seen.add(identity)
                known_scenarios.append(identity)

    child_titles = [c["title"] for c in view.get("child_documents", [])]
    stats: dict[str, Any] = {
        "tax_key": resolved_tax_key,
        "source_document": view["title"],
        "child_documents": child_titles,
        "missing_parent": missing_parent,
        "batches": len(batches),
        "failed_batches": failed_batches,
        "provisions_supplied": len(all_allowed_nodes),
        "requirements": len(all_requirements),
        "unresolved": all_unresolved,
        "truncated_nodes": 0,
        "dropped_citations": 0,
        "uncited_fields": 0,
    }

    if failed_batches and not all_requirements:
        details = "; ".join(f"#{f['batch']} {f['error']}" for f in failed_batches)
        raise LLMBatchFailure(
            f"all {len(batches)} batch(es) failed for {view['title']}: {details}"
        )

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
    if failed_batches:
        logger.warning(
            "%d of %d batches failed for %s; the matrix is incomplete",
            len(failed_batches),
            len(batches),
            view["title"],
        )
    logger.info(
        "Extracted %d requirement rows across %d batches for %s (%d citations dropped as unverifiable)",
        stats["requirements"],
        stats["batches"],
        resolved_tax_key,
        stats["dropped_citations"],
    )
    return stats


# ---------- internals ----------


def _render_batches(view: dict[str, Any]) -> list[list[tuple[str, set[str]]]]:
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

    budget = _batch_chars()
    batches: list[list[tuple[str, set[str]]]] = []
    current_group: list[tuple[str, set[str]]] = []
    current_chars = 0

    for block_text, block_nodes in blocks:
        block_len = len(block_text)
        # If adding this block exceeds budget (and current batch is not empty), finish current batch
        if current_chars + block_len > budget and current_group:
            batches.append(current_group)
            current_group = []
            current_chars = 0

        current_group.append((block_text, block_nodes))
        current_chars += block_len

    if current_group:
        batches.append(current_group)

    return batches


# Enough to steer wording without crowding out the provisions themselves. The
# list is ordered by first sighting, so the oldest — and most established —
# scenarios survive the cut.
_MAX_KNOWN_SCENARIOS = 60


def _strip_echoed_role(scenario: str | None) -> str:
    """Undo a scenario that swallowed the role from the known-scenarios list.

    Belt and braces alongside the labelled format: a model that concatenates
    anyway must not be able to mint 「情境｜角色」 as a fresh identity, because
    that row then never matches the real one and the list grows instead of
    converging.
    """
    text = (scenario or "").strip()
    for separator in ("｜", "|"):
        if separator in text:
            head = text.split(separator, 1)[0].strip()
            if head:
                logger.warning("Scenario echoed its role back; trimming: %r", text)
                return head
    return text


def _known_scenarios_section(known: list[tuple[str, str]]) -> str:
    if not known:
        return ""
    shown = known[:_MAX_KNOWN_SCENARIOS]
    # Labelled fields on separate lines, not one joined string. A 「scenario｜role」
    # line reads as a single value, and the model copied it verbatim into
    # `scenario` — producing 「個人（境內居住者）綜合所得稅申報｜個人 - 綜合所得稅」
    # as a brand new identity, which is the opposite of what the list is for.
    lines = "\n\n".join(
        f"{index}.\n   scenario: {scenario}\n   taxpayer_role: {role}"
        for index, (scenario, role) in enumerate(shown, start=1)
    )
    truncated = (
        f"\n（清單已截斷，另有 {len(known) - len(shown)} 個情境未列出）"
        if len(known) > len(shown)
        else ""
    )
    return (
        "\n## 前面批次已識別的情境\n\n"
        "以下情境已經整理過。本批條文若屬於其中任何一個，"
        "**必須逐字沿用該情境的 scenario 與 taxpayer_role 兩個欄位的值**，"
        "把本批條文的內容填進它的欄位，不要另創說法、不要新增一列。\n"
        "注意：scenario 與 taxpayer_role 是兩個獨立欄位，"
        "**不要把它們串成一個字串**。\n"
        "只有本批條文確實構成清單以外的新課稅情境時，才新增。\n\n"
        f"{lines}{truncated}\n"
    )


def _group_text(group: list[tuple[str, set[str]]]) -> str:
    return "\n".join(text for text, _ in group)


def _group_nodes(group: list[tuple[str, set[str]]]) -> set[str]:
    nodes: set[str] = set()
    for _, block_nodes in group:
        nodes.update(block_nodes)
    return nodes


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
                # Derived, not asked for: the model would otherwise re-emit the
                # law's name for every cited cell of every row.
                "title": node_key.split("#", 1)[0],
                # Trimmed on the way in as well as requested in the schema — a
                # model that ignores the limit must not be able to inflate the
                # stored row either.
                "quote": (getattr(citation, "quote", "") or "").strip()[:_MAX_QUOTE_CHARS],
            }
        )
    return kept, dropped


def _infer_tax_key(title: str, country: str = "CN") -> str:
    return classify(title, country=country).key


def _tax_name(tax_key: str) -> str:
    tax_type = by_key(tax_key)
    return tax_type.name_zh if tax_type else UNCLASSIFIED.name_zh
