"""Queries over the 申報規範 matrix, shared by the API and the dashboard."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from taxwatch.models import (
    Change,
    Document,
    FieldSource,
    RequirementField,
    RequirementStatus,
    TaxRequirement,
)
from taxwatch.requirements.fields import FIELD_SPECS, label
from taxwatch.taxonomy import TAX_TYPES, UNCLASSIFIED


class RequirementNotFound(LookupError):
    """Raised when no requirement row matches the requested identifier."""


def list_requirements(
    session: Session,
    *,
    country: str | None = None,
    tax_key: str | None = None,
) -> list[dict[str, Any]]:
    query = session.query(TaxRequirement)
    if country:
        query = query.filter(TaxRequirement.country == country)
    if tax_key:
        query = query.filter(TaxRequirement.tax_key == tax_key)

    rows = [_summarise(r) for r in query.all()]
    # Anything needing review first — that is the only reason to open this page
    # when nothing has changed.
    rows.sort(key=lambda r: (-r["fields_needing_review"], r["scenario"]))
    return rows


def get_requirement(session: Session, requirement_id: int) -> dict[str, Any]:
    requirement = session.get(TaxRequirement, requirement_id)
    if requirement is None:
        raise RequirementNotFound(str(requirement_id))

    by_key = {f.field_key: f for f in requirement.fields}
    source = (
        session.get(Document, requirement.source_document_id)
        if requirement.source_document_id
        else None
    )

    return {
        **_summarise(requirement),
        "source_document": (
            {"title": source.title, "external_id": source.external_id, "url": source.url}
            if source
            else None
        ),
        "model": requirement.model,
        "prompt_version": requirement.prompt_version,
        "notes": requirement.notes,
        # Always emit every column, present or not — a missing cell is itself
        # information, and hiding it makes the matrix look more complete than
        # it is.
        "fields": [_field_row(session, spec.key, by_key.get(spec.key)) for spec in FIELD_SPECS],
    }


def review_summary(session: Session, *, tax_key: str | None = None) -> dict[str, Any]:
    """What a reviewer needs to work through, newest problem first."""
    query = (
        session.query(RequirementField, TaxRequirement)
        .join(TaxRequirement, RequirementField.requirement_id == TaxRequirement.id)
        .filter(RequirementField.needs_review.is_(True))
    )
    if tax_key:
        query = query.filter(TaxRequirement.tax_key == tax_key)

    items = [
        {
            "requirement_id": requirement.id,
            "tax_key": requirement.tax_key,
            "tax_name": _tax_name(requirement.tax_key),
            "scenario": requirement.scenario,
            "taxpayer_role": requirement.taxpayer_role,
            "field_key": field.field_key,
            "field_label": label(field.field_key),
            "reason": field.review_reason,
            "change_id": field.stale_change_id,
            "value": field.value,
        }
        for field, requirement in query.all()
    ]
    items.sort(key=lambda i: (i["tax_name"], i["scenario"], i["field_label"]))
    return {"count": len(items), "items": items}


def update_field(
    session: Session,
    requirement_id: int,
    field_key: str,
    value: str,
    *,
    clear_flag: bool = True,
) -> dict[str, Any]:
    """Record a reviewer's edit. Manual edits outrank later LLM extractions."""
    requirement = session.get(TaxRequirement, requirement_id)
    if requirement is None:
        raise RequirementNotFound(str(requirement_id))

    field = next((f for f in requirement.fields if f.field_key == field_key), None)
    if field is None:
        field = RequirementField(requirement_id=requirement.id, field_key=field_key)
        session.add(field)
        session.flush()
        requirement.fields.append(field)

    field.value = value.strip()
    field.source = FieldSource.MANUAL
    if clear_flag:
        field.needs_review = False
        field.review_reason = ""
        field.stale_change_id = None

    if not any(f.needs_review for f in requirement.fields):
        requirement.status = RequirementStatus.REVIEWED

    session.commit()
    return get_requirement(session, requirement_id)


# ---------- internals ----------


def _summarise(requirement: TaxRequirement) -> dict[str, Any]:
    fields = requirement.fields
    cited = [f for f in fields if f.citations]
    return {
        "id": requirement.id,
        "country": requirement.country,
        "tax_key": requirement.tax_key,
        "tax_name": _tax_name(requirement.tax_key),
        "scenario": requirement.scenario,
        "taxpayer_role": requirement.taxpayer_role,
        "status": requirement.status.value,
        "field_count": len(fields),
        "cited_field_count": len(cited),
        "fields_needing_review": sum(1 for f in fields if f.needs_review),
        "average_confidence": (
            round(sum(f.confidence for f in cited) / len(cited), 3) if cited else None
        ),
        "updated_at": requirement.updated_at.isoformat() if requirement.updated_at else None,
    }


def _field_row(
    session: Session,
    field_key: str,
    field: RequirementField | None,
) -> dict[str, Any]:
    from taxwatch.requirements.fields import field_spec

    spec = field_spec(field_key)
    if field is None:
        return {
            "field_key": field_key,
            "label": label(field_key),
            "value": "",
            "citations": [],
            "confidence": None,
            "source": None,
            "needs_review": False,
            "review_reason": "",
            "derivable": bool(spec and spec.derivable),
            "monospace": bool(spec and spec.monospace),
            "missing": True,
        }

    change = session.get(Change, field.stale_change_id) if field.stale_change_id else None
    return {
        "field_key": field.field_key,
        "label": label(field.field_key),
        "value": field.value,
        "citations": field.citations,
        "confidence": field.confidence,
        "source": field.source.value,
        "needs_review": field.needs_review,
        "review_reason": field.review_reason,
        "change": (
            {"id": change.id, "node_key": change.node_key, "severity": change.severity.value}
            if change
            else None
        ),
        "derivable": bool(spec and spec.derivable),
        "monospace": bool(spec and spec.monospace),
        "missing": False,
    }


def _tax_name(tax_key: str) -> str:
    for tax_type in TAX_TYPES:
        if tax_type.key == tax_key:
            return tax_type.name_zh
    return UNCLASSIFIED.name_zh
