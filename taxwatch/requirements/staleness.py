"""Flag 申報規範 cells whose underlying provisions have moved.

This is the reason the matrix lives in the system rather than in a spreadsheet.
A spreadsheet cannot know that 增值税法第32条 was amended last night; the cell
that quoted it stays confidently wrong until someone happens to re-read the law.

Runs at the end of the pipeline, after diffs are recorded: every changed
provision is matched against the cells citing it, and only those cells are
flagged. Fields marked non-derivable are skipped — nothing in a diff can
confirm 「不適用特殊稅收優惠政策」, so flagging it would produce a review task
with no way to close it.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from taxwatch.graph.resolver import normalize_entity_key
from taxwatch.models import (
    Change,
    Document,
    FieldSource,
    RequirementField,
    RequirementStatus,
    TaxRequirement,
)
from taxwatch.requirements.fields import DERIVABLE_FIELD_KEYS, label

logger = logging.getLogger(__name__)


def flag_stale_fields(session: Session, changes: list[Change]) -> list[RequirementField]:
    """Mark every cell citing a changed provision as needing review.

    Returns the fields that were newly flagged, so a run can report them.
    """
    if not changes:
        return []

    by_node: dict[str, Change] = {}
    for change in changes:
        # Keep the most severe change per provision — that is the one a
        # reviewer needs to see first.
        key = normalize_entity_key(change.node_key)
        current = by_node.get(key)
        if current is None or _severity_rank(change) > _severity_rank(current):
            by_node[key] = change

    flagged: list[RequirementField] = []

    for field in _candidate_fields(session):
        if field.field_key not in DERIVABLE_FIELD_KEYS:
            continue

        hit = next(
            (
                by_node[normalize_entity_key(node_key)]
                for node_key in field.cited_node_keys
                if normalize_entity_key(node_key) in by_node
            ),
            None,
        )
        if hit is None:
            continue

        field.needs_review = True
        field.stale_change_id = hit.id
        field.review_reason = _reason(session, hit, field)
        field.requirement.status = RequirementStatus.STALE
        flagged.append(field)

    if flagged:
        session.flush()
        logger.info("Flagged %d requirement fields as stale", len(flagged))
    return flagged


def clear_review_flag(
    session: Session,
    field: RequirementField,
    *,
    value: str | None = None,
) -> None:
    """Record a reviewer's decision on a flagged cell.

    Accepting the cell as-is is a real outcome — an amendment can touch a
    provision without changing what the filer must do — so clearing the flag
    without editing the value is allowed.
    """
    if value is not None and value != field.value:
        field.value = value
        field.source = FieldSource.MANUAL

    field.needs_review = False
    field.review_reason = ""
    field.stale_change_id = None

    requirement = field.requirement
    if not any(f.needs_review for f in requirement.fields):
        requirement.status = RequirementStatus.REVIEWED
    session.flush()


def pending_reviews(session: Session, *, tax_key: str | None = None) -> list[RequirementField]:
    query = (
        session.query(RequirementField)
        .join(TaxRequirement, RequirementField.requirement_id == TaxRequirement.id)
        .filter(RequirementField.needs_review.is_(True))
    )
    if tax_key:
        query = query.filter(TaxRequirement.tax_key == tax_key)
    return query.all()


# ---------- internals ----------


def _candidate_fields(session: Session) -> list[RequirementField]:
    """Cells that cite something and are not already flagged."""
    return session.query(RequirementField).filter(RequirementField.needs_review.is_(False)).all()


def _severity_rank(change: Change) -> int:
    order = {"critical": 3, "major": 2, "minor": 1, "cosmetic": 0}
    return order.get(change.severity.value, 0)


def _reason(session: Session, change: Change, field: RequirementField) -> str:
    document = session.get(Document, change.document_id)
    title = document.title if document else "來源法規"
    return (
        f"{label(field.field_key)} 所依據的 {change.node_key}"
        f"（{title}）於 {change.detected_at:%Y-%m-%d} 偵測到"
        f"{change.change_type.value} 異動（{change.severity.value}），請重新確認。"
    )
