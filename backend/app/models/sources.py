"""Official-source, qualification, applicability, evidence, and embedding models."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from sqlalchemy.sql import text as sql_text

from app.models.base import Base, UUIDPrimaryKeyMixin, utc_now
from app.models.enums import (
    ExtractionStatus,
    MediaType,
    QualificationStatus,
    SourceStatus,
    database_enum,
)

if TYPE_CHECKING:
    from app.models.guidance import Conflict, CourseRelation, OfficeReferral
    from app.models.operations import IngestionRun, SourceEvent


class Source(UUIDPrimaryKeyMixin, Base):
    """A canonical public university document tracked through qualification."""

    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint("canonical_url ~ '^https://'", name="canonical_url_https"),
        CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
    )

    canonical_url: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    media_type: Mapped[MediaType] = mapped_column(
        database_enum(MediaType, name="media_type"),
        nullable=False,
    )
    status: Mapped[SourceStatus] = mapped_column(
        database_enum(SourceStatus, name="source_status"),
        nullable=False,
        default=SourceStatus.CANDIDATE,
        server_default=SourceStatus.CANDIDATE.value,
    )

    versions: Mapped[list[SourceVersion]] = relationship(
        back_populates="source",
        order_by="SourceVersion.fetched_at",
    )
    events: Mapped[list[SourceEvent]] = relationship(
        back_populates="source",
        order_by="SourceEvent.timestamp",
    )
    ingestion_runs: Mapped[list[IngestionRun]] = relationship(
        back_populates="source",
        order_by="IngestionRun.started_at",
    )


class SourceVersion(UUIDPrimaryKeyMixin, Base):
    """An immutable fetched representation of one canonical source."""

    __tablename__ = "source_versions"
    __table_args__ = (
        UniqueConstraint("source_id", "content_sha256", name="source_content_hash"),
        Index("ix_source_versions_content_sha256", "content_sha256"),
        CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="content_sha256_lower_hex",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from",
            name="effective_period_ordered",
        ),
        CheckConstraint("length(btrim(parser_version)) > 0", name="parser_version_not_blank"),
    )

    source_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    extraction_status: Mapped[ExtractionStatus] = mapped_column(
        database_enum(ExtractionStatus, name="extraction_status"),
        nullable=False,
        default=ExtractionStatus.PENDING,
        server_default=ExtractionStatus.PENDING.value,
    )
    parser_version: Mapped[str] = mapped_column(String(128), nullable=False)

    source: Mapped[Source] = relationship(back_populates="versions")
    qualifications: Mapped[list[Qualification]] = relationship(
        back_populates="version",
        order_by="Qualification.checked_at",
    )
    applicability_records: Mapped[list[Applicability]] = relationship(
        back_populates="version",
    )
    evidence_blocks: Mapped[list[EvidenceBlock]] = relationship(
        back_populates="version",
        order_by="EvidenceBlock.ordinal",
    )
    ingestion_runs: Mapped[list[IngestionRun]] = relationship(back_populates="version")


class Qualification(UUIDPrimaryKeyMixin, Base):
    """An append-only automated qualification result for a source version."""

    __tablename__ = "qualifications"
    __table_args__ = (
        UniqueConstraint("version_id", "rule_version", "checked_at", name="version_rule_check"),
        Index(
            "ix_qualifications_fresh_passed",
            "version_id",
            "valid_until",
            "checked_at",
            postgresql_where=sql_text("status = 'passed'"),
        ),
        CheckConstraint(
            "valid_until >= checked_at AND "
            "valid_until <= checked_at + INTERVAL '24 hours'",
            name="validity_within_24_hours",
        ),
        CheckConstraint("length(btrim(rule_version)) > 0", name="rule_version_not_blank"),
        CheckConstraint("jsonb_typeof(check_results) = 'object'", name="check_results_object"),
        CheckConstraint(
            "jsonb_typeof(provenance_evidence) = 'object'",
            name="provenance_evidence_object",
        ),
        CheckConstraint(
            "jsonb_typeof(applicability_evidence) = 'object'",
            name="applicability_evidence_object",
        ),
    )

    version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("source_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    rule_version: Mapped[str] = mapped_column(String(128), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[QualificationStatus] = mapped_column(
        database_enum(QualificationStatus, name="qualification_status"),
        nullable=False,
    )
    check_results: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=False,
        default=dict,
        server_default=sql_text("'{}'::jsonb"),
    )
    provenance_evidence: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=False,
        default=dict,
        server_default=sql_text("'{}'::jsonb"),
    )
    applicability_evidence: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=False,
        default=dict,
        server_default=sql_text("'{}'::jsonb"),
    )

    version: Mapped[SourceVersion] = relationship(back_populates="qualifications")


class Applicability(Base):
    """Source-backed scope for a topic within one source version."""

    __tablename__ = "applicability"
    __table_args__ = (
        Index(
            "ix_applicability_topic_institution_version",
            "topic",
            "institution",
            "version_id",
        ),
        Index(
            "ix_applicability_academic_scope",
            "campus",
            "student_level",
            "program",
            "version_id",
        ),
        Index(
            "ix_applicability_term_scope",
            "term",
            "session",
            "catalog_year",
            "version_id",
        ),
        CheckConstraint("length(btrim(topic)) > 0", name="topic_not_blank"),
        CheckConstraint("length(btrim(institution)) > 0", name="institution_not_blank"),
        CheckConstraint("cardinality(evidence_block_ids) > 0", name="has_evidence_blocks"),
    )

    version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("source_versions.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    topic: Mapped[str] = mapped_column(String(128), primary_key=True)
    institution: Mapped[str] = mapped_column(String(255), nullable=False)
    campus: Mapped[str | None] = mapped_column(String(128))
    student_level: Mapped[str | None] = mapped_column(String(128))
    program: Mapped[str | None] = mapped_column(String(255))
    catalog_year: Mapped[str | None] = mapped_column(String(32))
    term: Mapped[str | None] = mapped_column(String(64))
    session: Mapped[str | None] = mapped_column(String(64))
    evidence_block_ids: Mapped[list[UUID]] = mapped_column(
        MutableList.as_mutable(ARRAY(PostgreSQLUUID(as_uuid=True))),
        nullable=False,
        default=list,
        server_default=sql_text("'{}'::uuid[]"),
    )

    version: Mapped[SourceVersion] = relationship(back_populates="applicability_records")


class EvidenceBlock(UUIDPrimaryKeyMixin, Base):
    """A complete semantic unit that may support an answer and citation."""

    __tablename__ = "evidence_blocks"
    __table_args__ = (
        UniqueConstraint("version_id", "ordinal", name="version_ordinal"),
        Index("ix_evidence_blocks_topic_version", "topic_key", "version_id"),
        Index(
            "ix_evidence_blocks_scope_gin",
            "scope",
            postgresql_using="gin",
            postgresql_ops={"scope": "jsonb_path_ops"},
        ),
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        CheckConstraint("page IS NULL OR page >= 1", name="page_positive"),
        CheckConstraint("length(btrim(text)) > 0", name="text_not_blank"),
        CheckConstraint("length(btrim(topic_key)) > 0", name="topic_key_not_blank"),
        CheckConstraint(
            "jsonb_typeof(structured_content) IN ('object', 'array')",
            name="structured_content_container",
        ),
        CheckConstraint("jsonb_typeof(scope) = 'object'", name="scope_object"),
    )

    version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("source_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    heading_path: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(ARRAY(Text)),
        nullable=False,
        default=list,
        server_default=sql_text("'{}'::text[]"),
    )
    page: Mapped[int | None] = mapped_column(Integer)
    anchor: Mapped[str | None] = mapped_column(String(512))
    text: Mapped[str] = mapped_column(Text, nullable=False)
    structured_content: Mapped[dict[str, object] | list[object]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default=sql_text("'{}'::jsonb"),
    )
    topic_key: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=False,
        default=dict,
        server_default=sql_text("'{}'::jsonb"),
    )

    version: Mapped[SourceVersion] = relationship(back_populates="evidence_blocks")
    embeddings: Mapped[list[Embedding]] = relationship(back_populates="block")
    course_relations: Mapped[list[CourseRelation]] = relationship(back_populates="evidence_block")
    office_referrals: Mapped[list[OfficeReferral]] = relationship(back_populates="evidence_block")
    conflicts: Mapped[list[Conflict]] = relationship(
        secondary="conflict_evidence_blocks",
        back_populates="evidence_blocks",
    )


class Embedding(Base):
    """A normalized 384-dimensional vector derived only from public evidence."""

    __tablename__ = "embeddings"
    __table_args__ = (
        Index("ix_embeddings_model_revision_block", "model_revision", "block_id"),
        CheckConstraint("dimensions = 384", name="dimensions_384"),
        CheckConstraint("length(btrim(model_revision)) > 0", name="model_revision_not_blank"),
    )

    block_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("evidence_blocks.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    model_revision: Mapped[str] = mapped_column(String(255), primary_key=True)
    dimensions: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=384,
        server_default="384",
    )
    vector: Mapped[list[float]] = mapped_column(VECTOR(384), nullable=False)

    block: Mapped[EvidenceBlock] = relationship(back_populates="embeddings")
