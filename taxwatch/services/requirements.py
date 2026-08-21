"""Queries over the 申報規範 matrix, shared by the API and the dashboard."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy.orm import Session, joinedload

from taxwatch.graph.resolver import normalize_entity_key
from taxwatch.models import (
    Change,
    Document,
    FieldSource,
    RequirementField,
    RequirementStatus,
    TaxRequirement,
)
from taxwatch.requirements.fields import FIELD_SPECS, label
from taxwatch.taxonomy import UNCLASSIFIED, by_key


class RequirementNotFound(LookupError):
    """Raised when no requirement row matches the requested identifier."""


def affected_by_node_keys(
    session: Session,
    node_keys: Iterable[str],
) -> dict[str, list[dict[str, Any]]]:
    """Find requirement fields affected by given provision node keys.

    Returns a mapping of normalized_node_key -> list of affected field details.
    Builds an in-memory index in a single query across all RequirementFields
    with citations without filtering by needs_review (reviewed cells citing
    the provision are still affected).
    """
    target_keys = {normalize_entity_key(k) for k in node_keys if k}
    if not target_keys:
        return {}

    # Query all RequirementFields with their parent TaxRequirement eagerly loaded
    fields = (
        session.query(RequirementField)
        .options(joinedload(RequirementField.requirement))
        .all()
    )

    index: dict[str, list[dict[str, Any]]] = {k: [] for k in target_keys}

    for field in fields:
        req = field.requirement
        if not req:
            continue

        field_info = {
            "requirement_id": req.id,
            "country": req.country,
            "tax_key": req.tax_key,
            "tax_name": _tax_name(req.tax_key),
            "taxpayer_role": req.taxpayer_role,
            "scenario": req.scenario,
            "field_key": field.field_key,
            "field_label": label(field.field_key),
            "value": field.value,
            "needs_review": field.needs_review,
            "source": field.source.value if field.source else None,
        }

        matched_keys = set()
        for raw_k in field.cited_node_keys:
            norm_k = normalize_entity_key(raw_k)
            if norm_k in target_keys and norm_k not in matched_keys:
                index[norm_k].append(field_info)
                matched_keys.add(norm_k)

    return index


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
    _mark_unconfirmed(rows)
    # Anything needing review first — that is the only reason to open this page
    # when nothing has changed.
    rows.sort(key=lambda r: (-r["fields_needing_review"], r["scenario"]))
    return rows


def _mark_unconfirmed(rows: list[dict[str, Any]]) -> None:
    """Flag rows the most recent extraction of their tax type did not produce.

    Coverage varies between runs — 虧損扣除 appears in one pass and OBU in the
    next — and because upsert matches on identity_key, a missed row is neither
    duplicated nor deleted. It stays, unrefreshed, looking exactly like a row
    that was just confirmed. That is the one thing the matrix must not do: a
    filer reading it cannot tell which cells were checked against current law.

    Every row of one extraction shares its timestamp, so "the latest pass" is an
    exact comparison rather than a guess at how long a run takes.
    """
    latest: dict[tuple[str, str], str] = {}
    for row in rows:
        seen = row.get("last_seen_at")
        if not seen:
            continue
        key = (row["country"], row["tax_key"])
        if seen > latest.get(key, ""):
            latest[key] = seen

    for row in rows:
        seen = row.get("last_seen_at")
        newest = latest.get((row["country"], row["tax_key"]))
        # No run has ever stamped this tax type: nothing to be behind of.
        row["unconfirmed"] = bool(newest) and seen != newest


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


def review_summary(
    session: Session,
    *,
    country: str | None = None,
    tax_key: str | None = None,
) -> dict[str, Any]:
    """What a reviewer needs to work through, newest problem first."""
    query = (
        session.query(RequirementField, TaxRequirement)
        .join(TaxRequirement, RequirementField.requirement_id == TaxRequirement.id)
        .filter(RequirementField.needs_review.is_(True))
    )
    if country:
        query = query.filter(TaxRequirement.country == country.upper())
    if tax_key:
        query = query.filter(TaxRequirement.tax_key == tax_key)

    items = [
        {
            "requirement_id": requirement.id,
            "country": requirement.country,
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
        "identity_key": requirement.identity_key,
        "dimensions": requirement.dimensions or {},
        "status": requirement.status.value,
        "field_count": len(fields),
        "cited_field_count": len(cited),
        "fields_needing_review": sum(1 for f in fields if f.needs_review),
        "average_confidence": (
            round(sum(f.confidence for f in cited) / len(cited), 3) if cited else None
        ),
        "updated_at": requirement.updated_at.isoformat() if requirement.updated_at else None,
        "last_seen_at": (
            requirement.last_seen_at.isoformat() if requirement.last_seen_at else None
        ),
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
    tax_type = by_key(tax_key)
    return tax_type.name_zh if tax_type else UNCLASSIFIED.name_zh
