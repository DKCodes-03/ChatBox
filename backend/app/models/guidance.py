"""Structured academic guidance, referral, and source-conflict models."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Index, String, Table, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func, text

from app.models.base import Base, UUIDPrimaryKeyMixin, utc_now
from app.models.enums import ConflictStatus, CourseRelationType, database_enum

if TYPE_CHECKING:
    from app.models.sources import EvidenceBlock


conflict_evidence_blocks = Table(
    "conflict_evidence_blocks",
    Base.metadata,
    Column(
        "conflict_id",
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("conflicts.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Column(
        "evidence_block_id",
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("evidence_blocks.id", ondelete="RESTRICT"),
        primary_key=True,
    ),
    Index(
        "ix_conflict_evidence_blocks_evidence_block_id",
        "evidence_block_id",
    ),
)


class CourseRelation(UUIDPrimaryKeyMixin, Base):
    """A source-backed prerequisite, corequisite, or equivalence relationship."""

    __tablename__ = "course_relations"
    __table_args__ = (
        Index("ix_course_relations_evidence_block_id", "evidence_block_id"),
        CheckConstraint("length(btrim(course_code)) > 0", name="course_code_not_blank"),
        CheckConstraint(
            "length(btrim(related_course_code)) > 0",
            name="related_course_code_not_blank",
        ),
        CheckConstraint("jsonb_typeof(group_expression) = 'object'", name="group_expression_object"),
    )

    evidence_block_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("evidence_blocks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    course_code: Mapped[str] = mapped_column(String(32), nullable=False)
    related_course_code: Mapped[str] = mapped_column(String(32), nullable=False)
    relation: Mapped[CourseRelationType] = mapped_column(
        database_enum(CourseRelationType, name="course_relation_type"),
        nullable=False,
    )
    group_expression: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    catalog_year: Mapped[str | None] = mapped_column(String(32))
    campus: Mapped[str | None] = mapped_column(String(128))

    evidence_block: Mapped[EvidenceBlock] = relationship(back_populates="course_relations")


class OfficeReferral(UUIDPrimaryKeyMixin, Base):
    """A contact route whose responsibilities are supported by eligible evidence."""

    __tablename__ = "office_referrals"
    __table_args__ = (
        Index("ix_office_referrals_evidence_block_id", "evidence_block_id"),
        CheckConstraint("length(btrim(office_name)) > 0", name="office_name_not_blank"),
        CheckConstraint("length(btrim(responsibilities)) > 0", name="responsibilities_not_blank"),
        CheckConstraint("length(btrim(contact_label)) > 0", name="contact_label_not_blank"),
        CheckConstraint(
            "contact_url ~ '^(https://|mailto:|tel:)'",
            name="contact_url_supported_scheme",
        ),
        CheckConstraint("jsonb_typeof(scope) = 'object'", name="scope_object"),
    )

    office_name: Mapped[str] = mapped_column(String(255), nullable=False)
    responsibilities: Mapped[str] = mapped_column(Text, nullable=False)
    contact_label: Mapped[str] = mapped_column(String(255), nullable=False)
    contact_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    evidence_block_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("evidence_blocks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    scope: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )

    evidence_block: Mapped[EvidenceBlock] = relationship(back_populates="office_referrals")


class Conflict(UUIDPrimaryKeyMixin, Base):
    """A disputed topic/scope that blocks claims until source-backed resolution."""

    __tablename__ = "conflicts"
    __table_args__ = (
        CheckConstraint("length(btrim(topic_key)) > 0", name="topic_key_not_blank"),
        CheckConstraint("jsonb_typeof(scope) = 'object'", name="scope_object"),
        CheckConstraint(
            "(status = 'unresolved' AND resolved_at IS NULL "
            "AND cardinality(supersession_evidence_ids) = 0) OR "
            "(status = 'resolved' AND resolved_at IS NOT NULL "
            "AND cardinality(supersession_evidence_ids) > 0)",
            name="resolution_is_source_backed",
        ),
        CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= detected_at",
            name="resolution_after_detection",
        ),
    )

    topic_key: Mapped[str] = mapped_column(String(128), nullable=False)
    scope: Mapped[dict[str, object]] = mapped_column(
        MutableDict.as_mutable(JSONB),
        nullable=False,
        default=dict,
        server_default=text("'{}'::jsonb"),
    )
    status: Mapped[ConflictStatus] = mapped_column(
        database_enum(ConflictStatus, name="conflict_status"),
        nullable=False,
        default=ConflictStatus.UNRESOLVED,
        server_default=ConflictStatus.UNRESOLVED.value,
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supersession_evidence_ids: Mapped[list[UUID]] = mapped_column(
        MutableList.as_mutable(ARRAY(PostgreSQLUUID(as_uuid=True))),
        nullable=False,
        default=list,
        server_default=text("'{}'::uuid[]"),
    )

    evidence_blocks: Mapped[list[EvidenceBlock]] = relationship(
        secondary=conflict_evidence_blocks,
        back_populates="conflicts",
    )
