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
        back_populates="document",
        order_by="Snapshot.fetched_at.desc()",
    )

    __table_args__ = (UniqueConstraint("source_id", "external_id", name="uq_doc_source_ext"),)


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # When the issuing authority dated this version (成文/發布日期). Distinct from
    # fetched_at: a first crawl pulls in decades of law at once, and ordering
    # those by crawl time collapses the whole corpus onto today.
    issued_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    raw_path: Mapped[str] = mapped_column(Text, default="")

    document: Mapped[Document] = relationship(back_populates="snapshots")
    provisions: Mapped[list[ProvisionNode]] = relationship(back_populates="snapshot")

    __table_args__ = (
        Index("ix_snapshot_doc_fetched", "document_id", "fetched_at"),
        Index("ix_snapshot_doc_issued", "document_id", "issued_at"),
    )

    @property
    def dated_at(self) -> datetime:
        """The date this version belongs at on a timeline.

        Falls back to the crawl time for sources that publish no date at all —
        wrong, but at least monotonic, and flagged by `has_official_date`.
        """
        return self.issued_at or self.fetched_at

    @property
    def has_official_date(self) -> bool:
        return self.issued_at is not None


class ProvisionNode(Base):
    __tablename__ = "provision_nodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshots.id"))
    node_key: Mapped[str] = mapped_column(String(500))
    heading: Mapped[str] = mapped_column(Text, default="")
    text: Mapped[str] = mapped_column(Text, default="")
    text_hash: Mapped[str] = mapped_column(String(64))

    snapshot: Mapped[Snapshot] = relationship(back_populates="provisions")

    __table_args__ = (Index("ix_prov_snapshot_key", "snapshot_id", "node_key"),)


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

    __table_args__ = (Index("ix_change_doc_detected", "document_id", "detected_at"),)


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
    source_change_id: Mapped[int | None] = mapped_column(ForeignKey("changes.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    from_entity: Mapped[LegalEntity] = relationship(foreign_keys=[from_entity_id])
    to_entity: Mapped[LegalEntity] = relationship(foreign_keys=[to_entity_id])

    __table_args__ = (
        UniqueConstraint(
            "from_entity_id",
            "to_entity_id",
            "relation_type",
            name="uq_relation_from_to_type",
        ),
    )


# ---------- Reference corpus ----------


class CorpusDocument(Base):
    """A document from an external reference corpus.

    Read-only background knowledge, kept apart from `documents`/`snapshots`
    (which are *our* crawl history). Its job is to answer "what does the text
    cited here actually say" without a web search, and to supply official
    tax-type and status labels for documents we would otherwise classify by
    heuristic.

    A corpus is a snapshot taken at `corpus_version`; `aging` is the status as
    of that date, not necessarily today's.
    """

    __tablename__ = "corpus_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    corpus_key: Mapped[str] = mapped_column(String(100))
    corpus_version: Mapped[str] = mapped_column(String(32), default="")
    document_number: Mapped[str] = mapped_column(String(300), default="")
    title: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String(100), default="")
    effect_level: Mapped[str] = mapped_column(String(100), default="")
    tax_type_raw: Mapped[str] = mapped_column(Text, default="")
    tax_keys: Mapped[list] = mapped_column(JSON, default=list)
    aging: Mapped[str] = mapped_column(String(50), default="")
    labels: Mapped[str] = mapped_column(Text, default="")
    issuing_department: Mapped[str] = mapped_column(Text, default="")
    written_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    url: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text, default="")

    __table_args__ = (
        Index("ix_corpus_docnum", "corpus_key", "document_number"),
        Index("ix_corpus_title", "corpus_key", "title"),
    )

    @property
    def is_repealed(self) -> bool:
        return self.aging in ("全文废止", "全文失效")


# ---------- Filing requirements (申報規範) ----------


class RequirementStatus(enum.StrEnum):
    DRAFT = "draft"  # LLM 剛抽取，未經人工確認
    REVIEWED = "reviewed"  # 人工確認過
    STALE = "stale"  # 所引用的條文已異動，待重新覆核


class FieldSource(enum.StrEnum):
    LLM = "llm"
    MANUAL = "manual"
    IMPORT = "import"


class TaxRequirement(Base):
    """One row of the 申報規範 matrix: what a filer must do in one situation.

    Identity is (tax type, scenario, taxpayer role) — 增值稅 alone is not
    actionable, because a 小規模納稅人 selling services faces a different rate,
    base and deadline than a 一般納稅人 selling goods.
    """

    __tablename__ = "tax_requirements"

    id: Mapped[int] = mapped_column(primary_key=True)
    country: Mapped[str] = mapped_column(String(10))
    tax_key: Mapped[str] = mapped_column(String(50))
    scenario: Mapped[str] = mapped_column(Text)
    taxpayer_role: Mapped[str] = mapped_column(Text, default="")

    status: Mapped[RequirementStatus] = mapped_column(
        Enum(RequirementStatus), default=RequirementStatus.DRAFT
    )
    # The law version this was extracted from, so a reviewer can tell whether
    # they are looking at guidance built on current text.
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id"), nullable=True
    )
    model: Mapped[str] = mapped_column(String(200), default="")
    prompt_version: Mapped[str] = mapped_column(String(32), default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    fields: Mapped[list[RequirementField]] = relationship(
        back_populates="requirement",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "country",
            "tax_key",
            "scenario",
            "taxpayer_role",
            name="uq_requirement_identity",
        ),
    )


class RequirementField(Base):
    """One cell of the matrix, with its own citations and review state.

    Stored per field rather than as columns on the requirement because staleness
    is per field: a rate change invalidates the rate and the formula, but says
    nothing about the filing deadline. Flagging the whole row would make every
    amendment force a full re-review, which is how a review process gets ignored.
    """

    __tablename__ = "requirement_fields"

    id: Mapped[int] = mapped_column(primary_key=True)
    requirement_id: Mapped[int] = mapped_column(ForeignKey("tax_requirements.id"))
    field_key: Mapped[str] = mapped_column(String(50))
    value: Mapped[str] = mapped_column(Text, default="")

    # [{"node_key": "增值税法#32", "title": "...", "url": "...", "quote": "..."}]
    citations: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[FieldSource] = mapped_column(Enum(FieldSource), default=FieldSource.LLM)

    needs_review: Mapped[bool] = mapped_column(default=False)
    review_reason: Mapped[str] = mapped_column(Text, default="")
    # The change that invalidated this cell, so the UI can link to the diff.
    stale_change_id: Mapped[int | None] = mapped_column(ForeignKey("changes.id"), nullable=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    requirement: Mapped[TaxRequirement] = relationship(back_populates="fields")

    __table_args__ = (
        UniqueConstraint("requirement_id", "field_key", name="uq_field_per_requirement"),
        Index("ix_field_review", "needs_review"),
    )

    @property
    def cited_node_keys(self) -> list[str]:
        return [c.get("node_key", "") for c in self.citations if c.get("node_key")]


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
