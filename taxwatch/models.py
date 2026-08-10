from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------- Enums ----------

class DocType(enum.StrEnum):
    STATUTE = "statute"
    REGULATION = "regulation"
    RULING = "ruling"
    INTERPRETATION = "interpretation"
    ANNOUNCEMENT = "announcement"
    NEWS = "news"


class ChangeType(enum.StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    MODIFIED = "modified"
    RENUMBERED = "renumbered"


class Severity(enum.StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    COSMETIC = "cosmetic"


class RelationType(enum.StrEnum):
    AUTHORITY_OF = "authority_of"
    INTERPRETS = "interprets"
    AMENDS = "amends"
    SUPERSEDES = "supersedes"
    CITES = "cites"


class ExtractionMethod(enum.StrEnum):
    REGEX = "regex"
    LLM = "llm"
    MANUAL = "manual"


class JobStatus(enum.StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TriggerType(enum.StrEnum):
    SCHEDULE = "schedule"
    MANUAL = "manual"
    API = "api"


# ---------- Document & Version tables ----------

class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True)
    country: Mapped[str] = mapped_column(String(10))
    connector: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(Text, default="")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(default=True)

    documents: Mapped[list[Document]] = relationship(back_populates="source")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    external_id: Mapped[str] = mapped_column(String(500))
    doc_type: Mapped[DocType] = mapped_column(Enum(DocType))
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, default="")
    issued_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    source: Mapped[Source] = relationship(back_populates="documents")
    snapshots: Mapped[list[Snapshot]] = relationship(
        back_populates="document", order_by="Snapshot.fetched_at.desc()",
    )

    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_doc_source_ext"),
    )


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    content_hash: Mapped[str] = mapped_column(String(64))
    raw_path: Mapped[str] = mapped_column(Text, default="")

    document: Mapped[Document] = relationship(back_populates="snapshots")
    provisions: Mapped[list[ProvisionNode]] = relationship(back_populates="snapshot")

    __table_args__ = (
        Index("ix_snapshot_doc_fetched", "document_id", "fetched_at"),
    )


class ProvisionNode(Base):
    __tablename__ = "provision_nodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshots.id"))
    node_key: Mapped[str] = mapped_column(String(500))
    heading: Mapped[str] = mapped_column(Text, default="")
    text: Mapped[str] = mapped_column(Text, default="")
    text_hash: Mapped[str] = mapped_column(String(64))

    snapshot: Mapped[Snapshot] = relationship(back_populates="provisions")

    __table_args__ = (
        Index("ix_prov_snapshot_key", "snapshot_id", "node_key"),
    )


class Change(Base):
    __tablename__ = "changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    from_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("snapshots.id"), nullable=True)
    to_snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshots.id"))
    node_key: Mapped[str] = mapped_column(String(500))
    change_type: Mapped[ChangeType] = mapped_column(Enum(ChangeType))
    diff_text: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[Severity] = mapped_column(Enum(Severity), default=Severity.MINOR)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    analysis: Mapped[Analysis | None] = relationship(back_populates="change", uselist=False)

    __table_args__ = (
        Index("ix_change_doc_detected", "document_id", "detected_at"),
    )


# ---------- Legal Graph ----------

class LegalEntity(Base):
    __tablename__ = "legal_entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_key: Mapped[str] = mapped_column(String(500), unique=True)
    entity_type: Mapped[DocType] = mapped_column(Enum(DocType))
    canonical_title: Mapped[str] = mapped_column(Text, default="")
    current_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class LegalRelation(Base):
    __tablename__ = "legal_relations"

    id: Mapped[int] = mapped_column(primary_key=True)
    from_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"))
    to_entity_id: Mapped[int] = mapped_column(ForeignKey("legal_entities.id"))
    relation_type: Mapped[RelationType] = mapped_column(Enum(RelationType))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    evidence_text: Mapped[str] = mapped_column(Text, default="")
    extracted_by: Mapped[ExtractionMethod] = mapped_column(Enum(ExtractionMethod))
    source_change_id: Mapped[int | None] = mapped_column(
        ForeignKey("changes.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    from_entity: Mapped[LegalEntity] = relationship(foreign_keys=[from_entity_id])
    to_entity: Mapped[LegalEntity] = relationship(foreign_keys=[to_entity_id])

    __table_args__ = (
        UniqueConstraint(
            "from_entity_id", "to_entity_id", "relation_type",
            name="uq_relation_from_to_type",
        ),
    )


# ---------- Analysis ----------

class Analysis(Base):
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    change_id: Mapped[int] = mapped_column(ForeignKey("changes.id"), unique=True)
    summary_zh: Mapped[str] = mapped_column(Text, default="")
    effective_date: Mapped[str] = mapped_column(String(50), default="")
    affected_parties: Mapped[list] = mapped_column(JSON, default=list)
    parent_law_impact: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    model: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    change: Mapped[Change] = relationship(back_populates="analysis")


# ---------- Job tracking ----------

class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_type: Mapped[str] = mapped_column(String(100))
    trigger: Mapped[TriggerType] = mapped_column(Enum(TriggerType))
    source_key: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.PENDING)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    stats: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
