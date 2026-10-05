"""Immutable source-version persistence and reason-coded extraction quarantine."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

from bs4.dammit import UnicodeDammit
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.ingestion.discovery import DiscoveredDocument
from app.ingestion.extract import (
    ExtractedDocument,
    ExtractionFailureReason,
)
from app.models.enums import (
    ExtractionStatus,
    IngestionStatus,
    SourceEventType,
    SourceStatus,
)
from app.models.operations import IngestionRun, SourceEvent
from app.models.sources import Source, SourceVersion

Clock = Callable[[], datetime]


class VersioningError(RuntimeError):
    """Base error for sanitized source-version failures."""


class VersionInputError(VersioningError):
    """Source, discovery, and extraction inputs do not describe the same document."""


class VersionConflictError(VersioningError):
    """An existing immutable hash has a different stored representation or parser result."""


class ImmutableVersionError(VersioningError):
    """An operation attempted to update or delete an immutable source version."""


class VersionPersistenceError(VersioningError):
    """A source version could not be persisted without exposing database details."""


@dataclass(frozen=True, slots=True)
class VersionWriteResult:
    version: SourceVersion
    created: bool
    extraction_status: ExtractionStatus
    quarantined: bool
    reason_codes: tuple[ExtractionFailureReason, ...]


def content_sha256(document: DiscoveredDocument) -> str:
    """Return the lowercase SHA-256 identity of the exact fetched public bytes."""

    if not isinstance(document, DiscoveredDocument):
        raise VersionInputError("a discovered document is required")
    return sha256(document.content).hexdigest()


def normalized_source_content(
    document: DiscoveredDocument,
    extraction: ExtractedDocument,
) -> str:
    """Return deterministic text suitable for the immutable SourceVersion content field."""

    _validate_document_pair(document, extraction)
    if document.media_type.value == "html":
        decoded = UnicodeDammit(document.content, is_html=True).unicode_markup
        if decoded is None:
            raise VersionInputError("HTML source encoding could not be normalized")
        content = decoded.removeprefix("\ufeff")
    else:
        content = extraction.text
    return content.replace("\r\n", "\n").replace("\r", "\n")


class SourceVersionService:
    """Create immutable versions and quarantine incomplete extraction results.

    The caller owns the surrounding transaction. This service flushes so UUIDs and constraint
    errors are known before it returns, but it never commits independently.
    """

    def __init__(self, session: Session, *, clock: Clock | None = None) -> None:
        if not isinstance(session, Session):
            raise TypeError("session must be a SQLAlchemy Session")
        self._session = session
        self._clock = clock or _utc_now

    def record(
        self,
        *,
        source: Source,
        document: DiscoveredDocument,
        extraction: ExtractedDocument,
        ingestion_run: IngestionRun | None = None,
    ) -> VersionWriteResult:
        """Persist or reuse one fetched-content version without mutating an existing version."""

        observed_at = _observed_at(self._clock())
        _validate_inputs(source, document, extraction, ingestion_run)
        normalized_content = normalized_source_content(document, extraction)
        digest = content_sha256(document)
        status, reasons = _extraction_outcome(extraction)

        existing = self._session.scalar(
            select(SourceVersion).where(
                SourceVersion.source_id == source.id,
                SourceVersion.content_sha256 == digest,
            )
        )
        if existing is not None:
            _assert_existing_matches(
                existing,
                content=normalized_content,
                parser_version=extraction.parser_version,
                extraction_status=status,
            )
            self._apply_outcome(
                source=source,
                version=existing,
                status=status,
                reasons=reasons,
                observed_at=observed_at,
                ingestion_run=ingestion_run,
            )
            self._flush()
            return VersionWriteResult(
                version=existing,
                created=False,
                extraction_status=status,
                quarantined=status is not ExtractionStatus.COMPLETE,
                reason_codes=reasons,
            )

        version = SourceVersion(
            source_id=source.id,
            content_sha256=digest,
            content=normalized_content,
            fetched_at=observed_at,
            extraction_status=status,
            parser_version=extraction.parser_version,
        )
        material_change = source.status is SourceStatus.ELIGIBLE and any(
            previous.content_sha256 != digest for previous in source.versions
        )
        self._session.add(version)
        self._flush()
        self._apply_outcome(
            source=source,
            version=version,
            status=status,
            reasons=reasons,
            observed_at=observed_at,
            ingestion_run=ingestion_run,
            material_change=material_change,
        )
        self._flush()
        return VersionWriteResult(
            version=version,
            created=True,
            extraction_status=status,
            quarantined=status is not ExtractionStatus.COMPLETE,
            reason_codes=reasons,
        )

    def _apply_outcome(
        self,
        *,
        source: Source,
        version: SourceVersion,
        status: ExtractionStatus,
        reasons: tuple[ExtractionFailureReason, ...],
        observed_at: datetime,
        ingestion_run: IngestionRun | None,
        material_change: bool = False,
    ) -> None:
        if status is not ExtractionStatus.COMPLETE:
            if source.status is not SourceStatus.WITHDRAWN:
                source.status = SourceStatus.QUARANTINED
            for reason in reasons:
                self._session.add(
                    SourceEvent(
                        source_id=source.id,
                        event_type=SourceEventType.CHANGE,
                        timestamp=observed_at,
                        rule_version=version.parser_version,
                        reason_code=reason.value,
                        evidence_ids=[],
                    )
                )

        elif material_change and source.status is not SourceStatus.WITHDRAWN:
            source.status = SourceStatus.QUARANTINED
            self._session.add(
                SourceEvent(
                    source_id=source.id,
                    event_type=SourceEventType.CHANGE,
                    timestamp=observed_at,
                    rule_version=version.parser_version,
                    reason_code="material_change_pending",
                    evidence_ids=[],
                )
            )

        if ingestion_run is None:
            return
        ingestion_run.completed_at = observed_at
        ingestion_run.version_id = version.id
        if status is ExtractionStatus.COMPLETE:
            ingestion_run.status = IngestionStatus.SUCCEEDED
            ingestion_run.bounded_error_code = None
        else:
            ingestion_run.status = IngestionStatus.FAILED
            ingestion_run.bounded_error_code = reasons[0].value

    def _flush(self) -> None:
        try:
            self._session.flush()
        except ImmutableVersionError:
            raise
        except SQLAlchemyError:
            raise VersionPersistenceError("source version could not be persisted") from None


def _validate_inputs(
    source: Source,
    document: DiscoveredDocument,
    extraction: ExtractedDocument,
    ingestion_run: IngestionRun | None,
) -> None:
    if not isinstance(source, Source) or not isinstance(source.id, UUID):
        raise VersionInputError("a persisted source is required")
    _validate_document_pair(document, extraction)
    if source.canonical_url != document.canonical_url:
        raise VersionInputError("source and discovered canonical URLs do not match")
    if source.media_type is not document.media_type:
        raise VersionInputError("source and discovered media types do not match")
    if not extraction.parser_version.strip() or len(extraction.parser_version) > 128:
        raise VersionInputError("parser version is invalid")
    if extraction.complete == bool(extraction.issues):
        raise VersionInputError("extraction completion and issues are inconsistent")
    if ingestion_run is not None:
        if not isinstance(ingestion_run, IngestionRun):
            raise VersionInputError("ingestion run is invalid")
        if ingestion_run.source_id != source.id:
            raise VersionInputError("ingestion run belongs to another source")
        if ingestion_run.status is not IngestionStatus.RUNNING:
            raise VersionInputError("ingestion run is already complete")


def _validate_document_pair(
    document: DiscoveredDocument,
    extraction: ExtractedDocument,
) -> None:
    if not isinstance(document, DiscoveredDocument) or not isinstance(
        extraction, ExtractedDocument
    ):
        raise VersionInputError("discovery and extraction results are required")
    if document.canonical_url != extraction.canonical_url:
        raise VersionInputError("discovery and extraction canonical URLs do not match")
    if document.media_type is not extraction.media_type:
        raise VersionInputError("discovery and extraction media types do not match")


def _extraction_outcome(
    extraction: ExtractedDocument,
) -> tuple[ExtractionStatus, tuple[ExtractionFailureReason, ...]]:
    if extraction.complete and extraction.units:
        return ExtractionStatus.COMPLETE, ()
    reasons = tuple(dict.fromkeys(issue.reason for issue in extraction.issues))
    if not reasons:
        reasons = (ExtractionFailureReason.EMPTY_DOCUMENT,)
    status = ExtractionStatus.INCOMPLETE if extraction.units else ExtractionStatus.FAILED
    return status, reasons


def _assert_existing_matches(
    version: SourceVersion,
    *,
    content: str,
    parser_version: str,
    extraction_status: ExtractionStatus,
) -> None:
    if (
        version.content != content
        or version.parser_version != parser_version
        or version.extraction_status is not extraction_status
    ):
        raise VersionConflictError(
            "existing immutable content hash has a different parser representation"
        )


def _observed_at(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise VersionInputError("version clock must return a timezone-aware timestamp")
    return value.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@event.listens_for(Session, "before_flush")
def _prevent_version_mutation(
    session: Session,
    _flush_context: object,
    _instances: object,
) -> None:
    """Reject ORM updates and deletes while allowing relationship-only collection changes."""

    if any(isinstance(item, SourceVersion) for item in session.deleted):
        raise ImmutableVersionError("source versions cannot be deleted")
    if any(
        isinstance(item, SourceVersion) and session.is_modified(item, include_collections=False)
        for item in session.dirty
    ):
        raise ImmutableVersionError("source versions cannot be updated")


def quarantine_reason_codes(
    extraction: ExtractedDocument,
) -> tuple[ExtractionFailureReason, ...]:
    """Expose the bounded extraction reasons without returning free-form parser details."""

    return _extraction_outcome(extraction)[1]


__all__ = [
    "ImmutableVersionError",
    "SourceVersionService",
    "VersionConflictError",
    "VersionInputError",
    "VersionPersistenceError",
    "VersionWriteResult",
    "VersioningError",
    "content_sha256",
    "normalized_source_content",
    "quarantine_reason_codes",
]
