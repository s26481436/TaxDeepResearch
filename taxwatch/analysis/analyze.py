"""Run LLM analysis on detected changes with legal graph context."""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from taxwatch.analysis.client import get_llm_client
from taxwatch.analysis.prompts import ANALYSIS_TEMPLATE, CONTEXT_TEMPLATE, SYSTEM_PROMPT
from taxwatch.analysis.schema import ChangeAnalysis
from taxwatch.graph.relations import get_entity_context
from taxwatch.models import Analysis, Change, Document, ProvisionNode

logger = logging.getLogger(__name__)


def analyze_change(session: Session, change: Change) -> Analysis:
    """Analyze a single change using LLM with legal graph context."""
    doc = session.get(Document, change.document_id)
    doc_title = doc.title if doc else ""

    old_text, new_text = _get_provision_texts(session, change)
    context_section = _build_context_section(session, change.node_key)

    user_prompt = ANALYSIS_TEMPLATE.format(
        document_title=doc_title,
        node_key=change.node_key,
        change_type=change.change_type.value,
        old_text=old_text or "(無舊版)",
        new_text=new_text or "(無新版)",
        diff_text=change.diff_text,
        context_section=context_section,
    )

    client = get_llm_client()
    result = client.generate_structured(
        system_prompt=SYSTEM_PROMPT,
        user_prompt=user_prompt,
        output_model=ChangeAnalysis,
    )

    analysis = Analysis(
        change_id=change.id,
        summary_zh=result.summary_zh,
        effective_date=result.effective_date,
        affected_parties=result.affected_parties,
        parent_law_impact=result.parent_law_impact,
        confidence=result.confidence,
        citations=[c.model_dump() for c in result.citations],
        model=client.model,
    )
    session.add(analysis)
    session.flush()

    logger.info(
        "Analyzed change %d: %s (confidence=%.2f)",
        change.id, change.node_key, result.confidence,
    )
    return analysis


def _get_provision_texts(session: Session, change: Change) -> tuple[str, str]:
    old_text = ""
    new_text = ""

    if change.from_snapshot_id:
        old_prov = (
            session.query(ProvisionNode)
            .filter_by(snapshot_id=change.from_snapshot_id, node_key=change.node_key)
            .first()
        )
        if old_prov:
            old_text = old_prov.text

    new_prov = (
        session.query(ProvisionNode)
        .filter_by(snapshot_id=change.to_snapshot_id, node_key=change.node_key)
        .first()
    )
    if new_prov:
        new_text = new_prov.text

    return old_text, new_text


def _build_context_section(session: Session, node_key: str) -> str:
    ctx = get_entity_context(session, node_key)
    if not ctx:
        return ""

    parent_texts: list[str] = []
    for rel, ent in ctx.get("parent_laws", []):
        parent_texts.append(f"- {ent.canonical_title} ({ent.entity_key})")

    related: list[str] = []
    for ent in ctx.get("siblings", []):
        related.append(f"- {ent.canonical_title} ({ent.entity_key})")

    if not parent_texts and not related:
        return ""

    return CONTEXT_TEMPLATE.format(
        parent_text="\n".join(parent_texts) if parent_texts else "(無)",
        related_rulings="\n".join(related) if related else "(無)",
    )
