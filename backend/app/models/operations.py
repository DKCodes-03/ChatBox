"""Source lifecycle, ingestion-run, and aggregate-only telemetry models."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func, text

from app.models.base import Base, UUIDPrimaryKeyMixin, utc_now
from app.models.enums import (
    IngestionStatus,
    MetricName,
    MetricOutcome,
    SourceEventType,
    database_enum,
)

if TYPE_CHECKING:
    from app.models.sources import Source, SourceVersion


class SourceEvent(UUIDPrimaryKeyMixin, Base):
    """An append-only, conversation-free source lifecycle record."""

    __tablename__ = "source_events"
    __table_args__ = (
        CheckConstraint("length(btrim(rule_version)) > 0", name="rule_version_not_blank"),
        CheckConstraint("length(btrim(reason_code)) > 0", name="reason_code_not_blank"),
    )

    source_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_type: Mapped[SourceEventType] = mapped_column(
        database_enum(SourceEventType, name="source_event_type"),
        nullable=False,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    rule_version: Mapped[str] = mapped_column(String(128), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(128), nullable=False)
    evidence_ids: Mapped[list[UUID]] = mapped_column(
        MutableList.as_mutable(ARRAY(PostgreSQLUUID(as_uuid=True))),
        nullable=False,
        default=list,
        server_default=text("'{}'::uuid[]"),
    )

    source: Mapped[Source] = relationship(back_populates="events")


class IngestionRun(UUIDPrimaryKeyMixin, Base):
    """A bounded public-source job with no student or conversation data."""

    __tablename__ = "ingestion_runs"
    __table_args__ = (
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="completion_after_start",
        ),
        CheckConstraint(
            "(status = 'running' AND completed_at IS NULL AND bounded_error_code IS NULL) OR "
            "(status = 'succeeded' AND completed_at IS NOT NULL "
            "AND bounded_error_code IS NULL) OR "
            "(status = 'failed' AND completed_at IS NOT NULL "
            "AND length(btrim(bounded_error_code)) > 0)",
            name="status_completion_consistent",
        ),
    )

    source_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=utc_now,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[IngestionStatus] = mapped_column(
        database_enum(IngestionStatus, name="ingestion_status"),
        nullable=False,
        default=IngestionStatus.RUNNING,
        server_default=IngestionStatus.RUNNING.value,
    )
    bounded_error_code: Mapped[str | None] = mapped_column(String(128))
    version_id: Mapped[UUID | None] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("source_versions.id", ondelete="RESTRICT"),
    )

    source: Mapped[Source] = relationship(back_populates="ingestion_runs")
    version: Mapped[SourceVersion | None] = relationship(back_populates="ingestion_runs")


class AggregateMetric(Base):
    """A bounded aggregate row incapable of storing text or student identifiers."""

    __tablename__ = "aggregate_metrics"
    __table_args__ = (
        Index(
            "ix_aggregate_metrics_name_outcome_hour",
            "bounded_metric_name",
            "outcome_enum",
            "hour_bucket",
        ),
        CheckConstraint(
            "hour_bucket = date_trunc('hour', hour_bucket, 'UTC')",
            name="hour_bucket_aligned",
        ),
        CheckConstraint("count >= 0", name="count_nonnegative"),
        CheckConstraint("sum_ms >= 0", name="sum_ms_nonnegative"),
        CheckConstraint("0 <= ALL (histogram_buckets)", name="histogram_nonnegative"),
    )

    hour_bucket: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    bounded_metric_name: Mapped[MetricName] = mapped_column(
        database_enum(MetricName, name="metric_name"),
        primary_key=True,
    )
    outcome_enum: Mapped[MetricOutcome] = mapped_column(
        database_enum(MetricOutcome, name="metric_outcome"),
        primary_key=True,
    )
    count: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
    sum_ms: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=0,
        server_default="0",
    )
    histogram_buckets: Mapped[list[int]] = mapped_column(
        MutableList.as_mutable(ARRAY(BigInteger)),
        nullable=False,
        default=list,
        server_default=text("'{}'::bigint[]"),
    )
