"""Final evidence authorization coordinated with source-governance row locks."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TypeVar
from uuid import UUID

from app.models.enums import ConflictStatus, ExtractionStatus, QualificationStatus, SourceStatus
from app.models.guidance import Conflict, conflict_evidence_blocks
from app.models.sources import EvidenceBlock, Qualification, Source, SourceVersion
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

Clock = Callable[[], datetime]
PublishedValue = TypeVar("PublishedValue")


class AuthorizationFailureReason(StrEnum):
    """Bounded reasons a previously retrieved answer can no longer be released."""

    CONFLICT = "conflict"
    EVIDENCE_MISSING = "evidence_missing"
    SOURCE_INELIGIBLE = "source_ineligible"
    SOURCE_WITHDRAWN = "source_withdrawn"
    VERSION_EXPIRED = "version_expired"
    VERSION_INCOMPLETE = "version_incomplete"
    VERSION_SUPERSEDED = "version_superseded"


class AuthorizationError(RuntimeError):
    """Authorization could not complete without exposing database details."""


@dataclass(frozen=True, slots=True)
class EvidenceAuthorizationReference:
    evidence_id: UUID
    version_id: UUID

    def __post_init__(self) -> None:
        if not isinstance(self.evidence_id, UUID) or not isinstance(self.version_id, UUID):
            raise TypeError("authorization references require UUIDs")


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    allowed: bool
    reason: AuthorizationFailureReason | None = None

    def __post_init__(self) -> None:
        if self.allowed == (self.reason is not None):
            raise ValueError("authorization decision is inconsistent")


@dataclass(frozen=True, slots=True)
class AuthorizationPublishResult[PublishedValue]:
    decision: AuthorizationDecision
    value: PublishedValue | None = None


class FinalAnswerAuthorizer:
    """Lock cited sources, recheck authority, and publish before releasing locks."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Clock | None = None,
    ) -> None:
        if not isinstance(engine, Engine):
            raise TypeError("engine must be a SQLAlchemy Engine")
        self._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        self._clock = clock or _utc_now

    def authorize_and_publish(
        self,
        references: Sequence[EvidenceAuthorizationReference],
        publisher: Callable[[], PublishedValue],
    ) -> AuthorizationPublishResult[PublishedValue]:
        """Invoke ``publisher`` inside the short transaction only when evidence remains usable."""

        normalized = _normalize_references(references)
        if not callable(publisher):
            raise TypeError("publisher must be callable")
        if not normalized:
            return AuthorizationPublishResult(
                decision=AuthorizationDecision(allowed=True),
                value=publisher(),
            )
        observed_at = _aware_utc(self._clock())
        try:
            with self._session_factory() as session, session.begin():
                decision = authorize_evidence(
                    session,
                    references=normalized,
                    observed_at=observed_at,
                    lock_sources=True,
                )
                if not decision.allowed:
                    return AuthorizationPublishResult(decision=decision)
                return AuthorizationPublishResult(decision=decision, value=publisher())
        except AuthorizationError:
            raise
        except SQLAlchemyError:
            raise AuthorizationError("answer authorization is unavailable") from None


def authorize_evidence(
    session: Session,
    *,
    references: Sequence[EvidenceAuthorizationReference],
    observed_at: datetime,
    lock_sources: bool,
    ignored_conflict_id: UUID | None = None,
) -> AuthorizationDecision:
    """Evaluate cited evidence in a transaction using the governance lock order."""

    if not isinstance(session, Session) or not isinstance(lock_sources, bool):
        raise TypeError("authorization session and lock mode are invalid")
    checked_at = _aware_utc(observed_at)
    normalized = _normalize_references(references)
    if not normalized:
        return AuthorizationDecision(allowed=True)

    version_ids = tuple(dict.fromkeys(item.version_id for item in normalized))
    evidence_ids = tuple(dict.fromkeys(item.evidence_id for item in normalized))
    try:
        source_ids = tuple(
            session.scalars(
                select(SourceVersion.source_id)
                .where(SourceVersion.id.in_(version_ids))
                .distinct()
                .order_by(SourceVersion.source_id)
            ).all()
        )
        if not source_ids:
            return AuthorizationDecision(False, AuthorizationFailureReason.EVIDENCE_MISSING)

        if lock_sources:
            # The read-only runtime role executes a narrow SECURITY DEFINER function that takes
            # shared row locks in UUID order. Governance updates take FOR UPDATE locks on the
            # same rows, so a committed withdrawal always precedes or follows publication.
            session.scalar(select(func.lock_sources_for_answer(list(source_ids))))
        source_statement = select(Source).where(Source.id.in_(source_ids)).order_by(Source.id)
        sources = tuple(session.scalars(source_statement).all())
        if len(sources) != len(source_ids):
            return AuthorizationDecision(False, AuthorizationFailureReason.EVIDENCE_MISSING)
        if any(source.status is SourceStatus.WITHDRAWN for source in sources):
            return AuthorizationDecision(False, AuthorizationFailureReason.SOURCE_WITHDRAWN)
        if any(source.status is SourceStatus.STALE for source in sources):
            return AuthorizationDecision(False, AuthorizationFailureReason.VERSION_EXPIRED)
        if any(source.status is not SourceStatus.ELIGIBLE for source in sources):
            return AuthorizationDecision(False, AuthorizationFailureReason.SOURCE_INELIGIBLE)

        evidence = tuple(
            session.scalars(
                select(EvidenceBlock)
                .where(EvidenceBlock.id.in_(evidence_ids))
                .order_by(EvidenceBlock.id)
            ).all()
        )
        evidence_by_id = {item.id: item for item in evidence}
        if len(evidence_by_id) != len(evidence_ids) or any(
            evidence_by_id.get(item.evidence_id) is None
            or evidence_by_id[item.evidence_id].version_id != item.version_id
            for item in normalized
        ):
            return AuthorizationDecision(False, AuthorizationFailureReason.EVIDENCE_MISSING)

        versions = tuple(
            session.scalars(
                select(SourceVersion)
                .where(SourceVersion.source_id.in_(source_ids))
                .order_by(SourceVersion.source_id, SourceVersion.fetched_at, SourceVersion.id)
            ).all()
        )
        version_by_id = {item.id: item for item in versions}
        if any(version_id not in version_by_id for version_id in version_ids):
            return AuthorizationDecision(False, AuthorizationFailureReason.EVIDENCE_MISSING)
        if any(
            version_by_id[version_id].extraction_status is not ExtractionStatus.COMPLETE
            for version_id in version_ids
        ):
            return AuthorizationDecision(False, AuthorizationFailureReason.VERSION_INCOMPLETE)

        qualifications = tuple(
            session.scalars(
                select(Qualification)
                .where(Qualification.version_id.in_(tuple(version_by_id)))
                .order_by(Qualification.version_id, Qualification.checked_at, Qualification.id)
            ).all()
        )
        current_versions = _current_versions(
            sources=sources,
            versions=versions,
            qualifications=qualifications,
            observed_at=checked_at,
        )
        for version_id in version_ids:
            version = version_by_id[version_id]
            decision = _version_decision(
                version,
                qualifications=qualifications,
                observed_at=checked_at,
            )
            if not decision.allowed:
                return decision
            if current_versions.get(version.source_id) != version.id:
                return AuthorizationDecision(
                    False,
                    AuthorizationFailureReason.VERSION_SUPERSEDED,
                )

        conflict_statement = (
            select(Conflict.id)
            .join(
                conflict_evidence_blocks,
                Conflict.id == conflict_evidence_blocks.c.conflict_id,
            )
            .where(
                conflict_evidence_blocks.c.evidence_block_id.in_(evidence_ids),
                Conflict.status == ConflictStatus.UNRESOLVED,
            )
        )
        if ignored_conflict_id is not None:
            conflict_statement = conflict_statement.where(Conflict.id != ignored_conflict_id)
        if session.scalar(conflict_statement.limit(1)) is not None:
            return AuthorizationDecision(False, AuthorizationFailureReason.CONFLICT)
    except SQLAlchemyError:
        raise AuthorizationError("answer authorization is unavailable") from None
    return AuthorizationDecision(allowed=True)


def _current_versions(
    *,
    sources: Sequence[Source],
    versions: Sequence[SourceVersion],
    qualifications: Sequence[Qualification],
    observed_at: datetime,
) -> dict[UUID, UUID]:
    candidates: defaultdict[UUID, list[tuple[datetime, datetime, UUID]]] = defaultdict(list)
    for version in versions:
        decision = _version_decision(
            version,
            qualifications=qualifications,
            observed_at=observed_at,
        )
        if not decision.allowed:
            continue
        latest = _latest_qualification(version.id, qualifications)
        if latest is None:
            continue
        candidates[version.source_id].append((version.fetched_at, latest.checked_at, version.id))

    current: dict[UUID, UUID] = {}
    for source in sources:
        choices = candidates.get(source.id, [])
        if not choices:
            continue
        choices.sort(key=lambda item: (item[0], item[1], str(item[2])))
        newest_fetched_at = choices[-1][0]
        newest = [item for item in choices if item[0] == newest_fetched_at]
        if len(newest) == 1:
            current[source.id] = newest[0][2]
    return current


def _version_decision(
    version: SourceVersion,
    *,
    qualifications: Sequence[Qualification],
    observed_at: datetime,
) -> AuthorizationDecision:
    if version.extraction_status is not ExtractionStatus.COMPLETE:
        return AuthorizationDecision(False, AuthorizationFailureReason.VERSION_INCOMPLETE)
    if version.effective_from is not None and _aware_utc(version.effective_from) > observed_at:
        return AuthorizationDecision(False, AuthorizationFailureReason.SOURCE_INELIGIBLE)
    if version.effective_to is not None and _aware_utc(version.effective_to) <= observed_at:
        return AuthorizationDecision(False, AuthorizationFailureReason.VERSION_EXPIRED)
    latest = _latest_qualification(version.id, qualifications)
    if latest is None:
        return AuthorizationDecision(False, AuthorizationFailureReason.SOURCE_INELIGIBLE)
    same_time = tuple(
        item
        for item in qualifications
        if item.version_id == version.id and item.checked_at == latest.checked_at
    )
    if any(item.status is not QualificationStatus.PASSED for item in same_time):
        return AuthorizationDecision(False, AuthorizationFailureReason.SOURCE_INELIGIBLE)
    if latest.status is not QualificationStatus.PASSED:
        return AuthorizationDecision(False, AuthorizationFailureReason.SOURCE_INELIGIBLE)
    if _aware_utc(latest.valid_until) <= observed_at:
        return AuthorizationDecision(False, AuthorizationFailureReason.VERSION_EXPIRED)
    return AuthorizationDecision(allowed=True)


def _latest_qualification(
    version_id: UUID,
    qualifications: Sequence[Qualification],
) -> Qualification | None:
    matching = tuple(item for item in qualifications if item.version_id == version_id)
    if not matching:
        return None
    return max(matching, key=lambda item: (item.checked_at, str(item.id)))


def _normalize_references(
    references: Sequence[EvidenceAuthorizationReference],
) -> tuple[EvidenceAuthorizationReference, ...]:
    if isinstance(references, (str, bytes)):
        raise TypeError("authorization references must be a sequence")
    normalized = tuple(references)
    if len(normalized) > 8 or any(
        not isinstance(item, EvidenceAuthorizationReference) for item in normalized
    ):
        raise TypeError("authorization references are invalid")
    identities = tuple((item.evidence_id, item.version_id) for item in normalized)
    if len(identities) != len(set(identities)):
        raise TypeError("authorization references must be unique")
    return normalized


def _aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise AuthorizationError("authorization clock must be timezone-aware")
    return value.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "AuthorizationDecision",
    "AuthorizationError",
    "AuthorizationFailureReason",
    "AuthorizationPublishResult",
    "EvidenceAuthorizationReference",
    "FinalAnswerAuthorizer",
    "authorize_evidence",
]
