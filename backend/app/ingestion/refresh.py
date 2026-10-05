"""Transactional source refresh, expiry, withdrawal, and supersession lifecycle."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID

from sqlalchemy import exists, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql.elements import ColumnElement

from app.ingestion.qualification import QUALIFICATION_RULE_VERSION
from app.models.enums import (
    ConflictStatus,
    ExtractionStatus,
    QualificationStatus,
    SourceEventType,
    SourceStatus,
)
from app.models.guidance import Conflict
from app.models.operations import SourceEvent
from app.models.sources import EvidenceBlock, Qualification, Source, SourceVersion
from app.retrieval.authorization import (
    AuthorizationError,
    EvidenceAuthorizationReference,
    authorize_evidence,
)

Clock = Callable[[], datetime]
REFRESH_INTERVAL = timedelta(hours=24)
MAX_DUE_SOURCES = 100
_REASON_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{0,127}$")


class RefreshFailureReason(StrEnum):
    MATERIAL_CHANGE_PENDING = "material_change_pending"
    QUALIFICATION_EXPIRED = "qualification_expired"
    REFRESH_FAILED = "refresh_failed"
    RESTORATION_REQUIRES_REQUALIFICATION = "restoration_requires_requalification"


class RefreshError(RuntimeError):
    """Base source-lifecycle error with no database or source content."""


class RefreshInputError(RefreshError):
    """A lifecycle command has invalid identifiers or unsupported evidence."""


class RefreshPersistenceError(RefreshError):
    """A source lifecycle transaction could not be loaded or flushed."""


class SupersessionError(RefreshError):
    """A conflict lacks explicit currently eligible supersession evidence."""


@dataclass(frozen=True, slots=True)
class LifecycleResult:
    source_id: UUID
    status: SourceStatus
    changed: bool
    reason_code: str
    evidence_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class SupersessionResult:
    conflict_id: UUID
    resolved_at: datetime
    evidence_ids: tuple[UUID, ...]


class SourceRefreshService:
    """Apply source governance updates without committing the caller-owned transaction."""

    def __init__(self, session: Session, *, clock: Clock | None = None) -> None:
        if not isinstance(session, Session):
            raise TypeError("session must be a SQLAlchemy Session")
        self._session = session
        self._clock = clock or _utc_now

    def due_sources(self, *, limit: int = MAX_DUE_SOURCES) -> tuple[Source, ...]:
        """Return non-withdrawn sources whose latest check is absent or at least 24 hours old."""

        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 100:
            raise RefreshInputError("refresh limit must be between one and one hundred")
        observed_at = _aware_utc(self._clock())
        last_check = (
            select(func.max(Qualification.checked_at))
            .join(SourceVersion, SourceVersion.id == Qualification.version_id)
            .where(SourceVersion.source_id == Source.id)
            .correlate(Source)
            .scalar_subquery()
        )
        try:
            return tuple(
                self._session.scalars(
                    select(Source)
                    .where(
                        Source.status != SourceStatus.WITHDRAWN,
                        or_(
                            last_check.is_(None),
                            last_check <= observed_at - REFRESH_INTERVAL,
                        ),
                    )
                    .order_by(last_check.asc().nullsfirst(), Source.id)
                    .limit(limit)
                ).all()
            )
        except SQLAlchemyError:
            raise RefreshPersistenceError("refresh schedule is unavailable") from None

    def expire_due(self) -> tuple[LifecycleResult, ...]:
        """Mark eligible sources stale when no current version has a fresh latest pass."""

        observed_at = _aware_utc(self._clock())
        current_pass = _current_pass_exists(observed_at)
        try:
            sources = tuple(
                self._session.scalars(
                    select(Source)
                    .where(
                        Source.status == SourceStatus.ELIGIBLE,
                        ~current_pass,
                    )
                    .order_by(Source.id)
                    .with_for_update()
                ).all()
            )
            results: list[LifecycleResult] = []
            for source in sources:
                evidence_ids = self._evidence_ids(source.id)
                source.status = SourceStatus.STALE
                reason = RefreshFailureReason.QUALIFICATION_EXPIRED.value
                self._add_event(
                    source=source,
                    event_type=SourceEventType.CHANGE,
                    reason_code=reason,
                    evidence_ids=evidence_ids,
                    observed_at=observed_at,
                )
                results.append(
                    LifecycleResult(
                        source_id=source.id,
                        status=source.status,
                        changed=True,
                        reason_code=reason,
                        evidence_ids=evidence_ids,
                    )
                )
            self._flush()
            return tuple(results)
        except RefreshError:
            raise
        except SQLAlchemyError:
            raise RefreshPersistenceError("qualification expiry could not be recorded") from None

    def record_refresh_failure(
        self,
        *,
        source_id: UUID,
        reason_code: str = RefreshFailureReason.REFRESH_FAILED.value,
    ) -> LifecycleResult:
        """Immediately block a source after any unsuccessful required refresh."""

        observed_at = _aware_utc(self._clock())
        reason = _reason_code(reason_code)
        source = self._locked_source(source_id)
        evidence_ids = self._evidence_ids(source.id)
        if source.status is SourceStatus.WITHDRAWN:
            return LifecycleResult(
                source_id=source.id,
                status=source.status,
                changed=False,
                reason_code=reason,
                evidence_ids=evidence_ids,
            )
        source.status = SourceStatus.STALE
        self._add_event(
            source=source,
            event_type=SourceEventType.CHANGE,
            reason_code=reason,
            evidence_ids=evidence_ids,
            observed_at=observed_at,
        )
        self._flush()
        return LifecycleResult(
            source_id=source.id,
            status=source.status,
            changed=True,
            reason_code=reason,
            evidence_ids=evidence_ids,
        )

    def mark_material_change(
        self,
        *,
        source_id: UUID,
        previous_version_id: UUID,
        replacement_version_id: UUID,
    ) -> LifecycleResult:
        """Suspend all source evidence until changed content independently requalifies."""

        observed_at = _aware_utc(self._clock())
        source = self._locked_source(source_id)
        versions = self._versions((previous_version_id, replacement_version_id))
        if (
            len(versions) != 2
            or any(version.source_id != source.id for version in versions.values())
            or versions[previous_version_id].content_sha256
            == versions[replacement_version_id].content_sha256
        ):
            raise RefreshInputError("material change versions are invalid")
        evidence_ids = self._evidence_ids_for_versions((replacement_version_id,))
        if source.status is SourceStatus.WITHDRAWN:
            return LifecycleResult(
                source_id=source.id,
                status=source.status,
                changed=False,
                reason_code=RefreshFailureReason.MATERIAL_CHANGE_PENDING.value,
                evidence_ids=evidence_ids,
            )
        source.status = SourceStatus.QUARANTINED
        reason = RefreshFailureReason.MATERIAL_CHANGE_PENDING.value
        self._add_event(
            source=source,
            event_type=SourceEventType.CHANGE,
            reason_code=reason,
            evidence_ids=evidence_ids,
            observed_at=observed_at,
        )
        self._flush()
        return LifecycleResult(
            source_id=source.id,
            status=source.status,
            changed=True,
            reason_code=reason,
            evidence_ids=evidence_ids,
        )

    def withdraw(self, *, source_id: UUID, reason_code: str) -> LifecycleResult:
        """Acquire the governance lock and transactionally disable a source."""

        observed_at = _aware_utc(self._clock())
        reason = _reason_code(reason_code)
        source = self._locked_source(source_id)
        evidence_ids = self._evidence_ids(source.id)
        if source.status is SourceStatus.WITHDRAWN:
            return LifecycleResult(
                source_id=source.id,
                status=source.status,
                changed=False,
                reason_code=reason,
                evidence_ids=evidence_ids,
            )
        source.status = SourceStatus.WITHDRAWN
        self._add_event(
            source=source,
            event_type=SourceEventType.WITHDRAWAL,
            reason_code=reason,
            evidence_ids=evidence_ids,
            observed_at=observed_at,
        )
        self._flush()
        return LifecycleResult(
            source_id=source.id,
            status=source.status,
            changed=True,
            reason_code=reason,
            evidence_ids=evidence_ids,
        )

    def restore(self, *, source_id: UUID) -> LifecycleResult:
        """Remove the withdrawal override while requiring a new automated qualification."""

        observed_at = _aware_utc(self._clock())
        source = self._locked_source(source_id)
        evidence_ids = self._evidence_ids(source.id)
        reason = RefreshFailureReason.RESTORATION_REQUIRES_REQUALIFICATION.value
        if source.status is not SourceStatus.WITHDRAWN:
            return LifecycleResult(
                source_id=source.id,
                status=source.status,
                changed=False,
                reason_code=reason,
                evidence_ids=evidence_ids,
            )
        source.status = SourceStatus.STALE
        self._add_event(
            source=source,
            event_type=SourceEventType.RESTORATION,
            reason_code=reason,
            evidence_ids=evidence_ids,
            observed_at=observed_at,
        )
        self._flush()
        return LifecycleResult(
            source_id=source.id,
            status=source.status,
            changed=True,
            reason_code=reason,
            evidence_ids=evidence_ids,
        )

    def resolve_supersession(
        self,
        *,
        conflict_id: UUID,
        evidence_ids: Sequence[UUID],
    ) -> SupersessionResult:
        """Resolve a conflict only with explicit currently authorized source evidence."""

        observed_at = _aware_utc(self._clock())
        normalized_ids = _evidence_id_sequence(evidence_ids)
        references = self._references(normalized_ids)
        try:
            # authorize_evidence locks source rows in sorted order before the conflict row lock.
            decision = authorize_evidence(
                self._session,
                references=references,
                observed_at=observed_at,
                lock_sources=True,
                ignored_conflict_id=conflict_id,
            )
            if not decision.allowed:
                raise SupersessionError("supersession evidence is not currently authorized")
            conflict = self._session.scalar(
                select(Conflict).where(Conflict.id == conflict_id).with_for_update()
            )
            if conflict is None or conflict.status is not ConflictStatus.UNRESOLVED:
                raise SupersessionError("conflict is unavailable for supersession")
            blocks = tuple(
                self._session.scalars(
                    select(EvidenceBlock).where(EvidenceBlock.id.in_(normalized_ids))
                ).all()
            )
            if len(blocks) != len(normalized_ids) or any(
                block.topic_key != conflict.topic_key for block in blocks
            ):
                raise SupersessionError("supersession evidence does not match the conflict")
            conflict.status = ConflictStatus.RESOLVED
            conflict.resolved_at = observed_at
            conflict.supersession_evidence_ids = list(normalized_ids)
            versions = self._versions(tuple(dict.fromkeys(item.version_id for item in references)))
            for source_id in sorted(
                {version.source_id for version in versions.values()},
                key=str,
            ):
                self._session.add(
                    SourceEvent(
                        source_id=source_id,
                        event_type=SourceEventType.CHANGE,
                        timestamp=observed_at,
                        rule_version=QUALIFICATION_RULE_VERSION,
                        reason_code="conflict_resolved_by_supersession",
                        evidence_ids=list(normalized_ids),
                    )
                )
            self._flush()
            return SupersessionResult(
                conflict_id=conflict.id,
                resolved_at=observed_at,
                evidence_ids=normalized_ids,
            )
        except (AuthorizationError, SQLAlchemyError):
            raise RefreshPersistenceError("supersession could not be recorded") from None

    def _locked_source(self, source_id: UUID) -> Source:
        if not isinstance(source_id, UUID):
            raise RefreshInputError("source identifier is invalid")
        try:
            source = self._session.scalar(
                select(Source).where(Source.id == source_id).with_for_update()
            )
        except SQLAlchemyError:
            raise RefreshPersistenceError("source lifecycle state is unavailable") from None
        if source is None:
            raise RefreshInputError("source does not exist")
        return source

    def _versions(self, version_ids: Sequence[UUID]) -> dict[UUID, SourceVersion]:
        if any(not isinstance(item, UUID) for item in version_ids):
            raise RefreshInputError("version identifier is invalid")
        try:
            versions = self._session.scalars(
                select(SourceVersion).where(SourceVersion.id.in_(version_ids))
            ).all()
        except SQLAlchemyError:
            raise RefreshPersistenceError("source versions are unavailable") from None
        return {version.id: version for version in versions}

    def _references(
        self,
        evidence_ids: Sequence[UUID],
    ) -> tuple[EvidenceAuthorizationReference, ...]:
        try:
            rows = self._session.execute(
                select(EvidenceBlock.id, EvidenceBlock.version_id).where(
                    EvidenceBlock.id.in_(evidence_ids)
                )
            ).all()
        except SQLAlchemyError:
            raise RefreshPersistenceError("supersession evidence is unavailable") from None
        if len(rows) != len(evidence_ids):
            raise SupersessionError("supersession evidence is missing")
        by_id = {row.id: row.version_id for row in rows}
        return tuple(
            EvidenceAuthorizationReference(evidence_id=item, version_id=by_id[item])
            for item in evidence_ids
        )

    def _evidence_ids(self, source_id: UUID) -> tuple[UUID, ...]:
        try:
            return tuple(
                self._session.scalars(
                    select(EvidenceBlock.id)
                    .join(SourceVersion, SourceVersion.id == EvidenceBlock.version_id)
                    .where(SourceVersion.source_id == source_id)
                    .order_by(EvidenceBlock.id)
                ).all()
            )
        except SQLAlchemyError:
            raise RefreshPersistenceError("source evidence is unavailable") from None

    def _evidence_ids_for_versions(self, version_ids: Sequence[UUID]) -> tuple[UUID, ...]:
        try:
            return tuple(
                self._session.scalars(
                    select(EvidenceBlock.id)
                    .where(EvidenceBlock.version_id.in_(version_ids))
                    .order_by(EvidenceBlock.id)
                ).all()
            )
        except SQLAlchemyError:
            raise RefreshPersistenceError("source evidence is unavailable") from None

    def _add_event(
        self,
        *,
        source: Source,
        event_type: SourceEventType,
        reason_code: str,
        evidence_ids: Sequence[UUID],
        observed_at: datetime,
    ) -> None:
        self._session.add(
            SourceEvent(
                source_id=source.id,
                event_type=event_type,
                timestamp=observed_at,
                rule_version=QUALIFICATION_RULE_VERSION,
                reason_code=reason_code,
                evidence_ids=list(evidence_ids),
            )
        )

    def _flush(self) -> None:
        try:
            self._session.flush()
        except SQLAlchemyError:
            raise RefreshPersistenceError("source lifecycle state could not be persisted") from None


def _current_pass_exists(observed_at: datetime) -> ColumnElement[bool]:
    latest_pass = aliased(Qualification, name="refresh_latest_pass")
    later_check = aliased(Qualification, name="refresh_later_check")
    same_time_failure = aliased(Qualification, name="refresh_same_time_failure")
    version = aliased(SourceVersion, name="refresh_version")
    return exists(
        select(1)
        .select_from(version)
        .join(latest_pass, latest_pass.version_id == version.id)
        .where(
            version.source_id == Source.id,
            version.extraction_status == ExtractionStatus.COMPLETE,
            latest_pass.status == QualificationStatus.PASSED,
            latest_pass.valid_until > observed_at,
            or_(version.effective_from.is_(None), version.effective_from <= observed_at),
            or_(version.effective_to.is_(None), version.effective_to > observed_at),
            ~exists(
                select(1).where(
                    later_check.version_id == latest_pass.version_id,
                    later_check.checked_at > latest_pass.checked_at,
                )
            ),
            ~exists(
                select(1).where(
                    same_time_failure.version_id == latest_pass.version_id,
                    same_time_failure.checked_at == latest_pass.checked_at,
                    same_time_failure.status != QualificationStatus.PASSED,
                )
            ),
        )
    )


def _reason_code(value: str) -> str:
    cleaned = value.strip() if isinstance(value, str) else ""
    if not _REASON_CODE_RE.fullmatch(cleaned):
        raise RefreshInputError("reason code is invalid")
    return cleaned


def _evidence_id_sequence(evidence_ids: Sequence[UUID]) -> tuple[UUID, ...]:
    if isinstance(evidence_ids, (str, bytes)):
        raise RefreshInputError("supersession evidence is invalid")
    normalized = tuple(evidence_ids)
    if (
        not normalized
        or len(normalized) > 8
        or any(not isinstance(item, UUID) for item in normalized)
    ):
        raise RefreshInputError("supersession evidence is invalid")
    if len(normalized) != len(set(normalized)):
        raise RefreshInputError("supersession evidence must be unique")
    return normalized


def _aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise RefreshInputError("refresh clock must be timezone-aware")
    return value.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "MAX_DUE_SOURCES",
    "REFRESH_INTERVAL",
    "LifecycleResult",
    "RefreshError",
    "RefreshFailureReason",
    "RefreshInputError",
    "RefreshPersistenceError",
    "SourceRefreshService",
    "SupersessionError",
    "SupersessionResult",
]
