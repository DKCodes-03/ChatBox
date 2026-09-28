"""Bounded values persisted by the public-corpus and aggregate models."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Enum as SQLAlchemyEnum


class SourceStatus(StrEnum):
    CANDIDATE = "candidate"
    ELIGIBLE = "eligible"
    QUARANTINED = "quarantined"
    STALE = "stale"
    WITHDRAWN = "withdrawn"


class MediaType(StrEnum):
    HTML = "html"
    PDF = "pdf"


class ExtractionStatus(StrEnum):
    PENDING = "pending"
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    FAILED = "failed"


class QualificationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"


class CourseRelationType(StrEnum):
    PREREQUISITE = "prerequisite"
    COREQUISITE = "corequisite"
    EQUIVALENT = "equivalent"


class ConflictStatus(StrEnum):
    UNRESOLVED = "unresolved"
    RESOLVED = "resolved"


class SourceEventType(StrEnum):
    QUALIFICATION = "qualification"
    CHANGE = "change"
    WITHDRAWAL = "withdrawal"
    RESTORATION = "restoration"


class IngestionStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class MetricName(StrEnum):
    CHAT_REQUEST = "chat_request"
    RETRIEVAL = "retrieval"
    GENERATION = "generation"


class MetricOutcome(StrEnum):
    ANSWER = "answer"
    PARTIAL = "partial"
    CLARIFICATION = "clarification"
    UNABLE = "unable"
    REJECTED = "rejected"
    ERROR = "error"


def database_enum(enum_class: type[StrEnum], *, name: str) -> SQLAlchemyEnum:
    """Store enum values as portable VARCHAR columns with database CHECK constraints."""

    return SQLAlchemyEnum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda members: [member.value for member in members],
    )
