"""Shared test fixtures: an in-memory database with a seeded document history."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from taxwatch.models import (
    Analysis,
    Base,
    Change,
    ChangeType,
    DocType,
    ExtractionMethod,  # noqa: F401  (imported so Enum tables register consistently)
    JobRun,
    JobStatus,
    ProvisionNode,
    Severity,
    Snapshot,
    Source,
    TriggerType,
)
from taxwatch.models import Document as DocumentModel


@pytest.fixture
def session() -> Session:
    # StaticPool + check_same_thread=False so TestClient's worker thread can
    # reuse the same in-memory database the fixture seeded.
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    try:
        yield db
    finally:
        db.close()


def make_snapshot(
    db: Session,
    document: DocumentModel,
    fetched_at: datetime,
    provisions: dict[str, str],
    content_hash: str,
) -> Snapshot:
    """Add one snapshot with the given {node_key: text} provisions."""
    snapshot = Snapshot(
        document_id=document.id,
        fetched_at=fetched_at,
        content_hash=content_hash,
    )
    db.add(snapshot)
    db.flush()
    for node_key, text in provisions.items():
        db.add(
            ProvisionNode(
                snapshot_id=snapshot.id,
                node_key=node_key,
                heading=node_key.split("#")[-1],
                text=text,
                text_hash=f"h-{hash(text) & 0xFFFFFFFF:08x}",
            )
        )
    db.flush()
    return snapshot


@pytest.fixture
def seeded(session: Session) -> dict:
    """A CN enterprise-income-tax law with three versions spanning six years.

    v1 (2020) → v2 (2023, art.28 rate changed) → v3 (2026, art.28 changed again
    plus a new art.43). Mirrors the "what does six years of drift look like"
    question the history API has to answer.
    """
    source = Source(
        key="cn-chinatax",
        country="CN",
        connector="cn_chinatax",
        description="国家税务总局",
        config={},
        enabled=True,
    )
    session.add(source)
    session.flush()

    doc = DocumentModel(
        source_id=source.id,
        external_id="cn-enterprise-income-tax-law",
        doc_type=DocType.STATUTE,
        title="中华人民共和国企业所得税法",
        url="https://example.gov.cn/law",
        issued_at=datetime(2020, 6, 1),
    )
    session.add(doc)
    session.flush()

    v1 = make_snapshot(
        session,
        doc,
        datetime(2020, 6, 1),
        {
            "企业所得税法#1": "在中华人民共和国境内，企业为企业所得税的纳税人。",
            "企业所得税法#28": "符合条件的小型微利企业，减按20%的税率征收企业所得税。",
        },
        "hash-v1",
    )

    v2 = make_snapshot(
        session,
        doc,
        datetime(2023, 6, 1),
        {
            "企业所得税法#1": "在中华人民共和国境内，企业为企业所得税的纳税人。",
            "企业所得税法#28": "符合条件的小型微利企业，减按15%的税率征收企业所得税。",
        },
        "hash-v2",
    )

    v3 = make_snapshot(
        session,
        doc,
        datetime(2026, 8, 1),
        {
            "企业所得税法#1": "在中华人民共和国境内，企业为企业所得税的纳税人。",
            "企业所得税法#28": "符合条件的小型微利企业，减按25%的税率征收企业所得税。",
            "企业所得税法#43": "制造业企业研发费用按实际发生额的100%在税前加计扣除。",
        },
        "hash-v3",
    )

    change_v2 = Change(
        document_id=doc.id,
        from_snapshot_id=v1.id,
        to_snapshot_id=v2.id,
        node_key="企业所得税法#28",
        change_type=ChangeType.MODIFIED,
        diff_text="- 减按20%\n+ 减按15%",
        severity=Severity.MAJOR,
        detected_at=datetime(2023, 6, 1),
    )
    change_v3 = Change(
        document_id=doc.id,
        from_snapshot_id=v2.id,
        to_snapshot_id=v3.id,
        node_key="企业所得税法#28",
        change_type=ChangeType.MODIFIED,
        diff_text="- 减按15%\n+ 减按25%",
        severity=Severity.CRITICAL,
        detected_at=datetime.utcnow() - timedelta(days=2),
    )
    session.add_all([change_v2, change_v3])
    session.flush()

    session.add(
        Analysis(
            change_id=change_v3.id,
            summary_zh="小型微利企业税率由15%调整为25%。",
            effective_date="2026-01-01",
            affected_parties=["小型微利企业", "制造业"],
            parent_law_impact="实施条例第92条需配合修订。",
            confidence=0.9,
            citations=[
                {"source": "企业所得税法", "article": "28", "url": "https://example.gov.cn/law"}
            ],
            model="test-model",
        )
    )

    session.add(
        JobRun(
            job_type="pipeline",
            trigger=TriggerType.MANUAL,
            source_key="cn-chinatax",
            status=JobStatus.COMPLETED,
            started_at=datetime.utcnow() - timedelta(minutes=5),
            finished_at=datetime.utcnow() - timedelta(minutes=4),
            stats={"documents": 1, "changes": 1},
        )
    )
    session.commit()

    return {
        "source": source,
        "document": doc,
        "snapshots": [v1, v2, v3],
        "changes": [change_v2, change_v3],
    }
