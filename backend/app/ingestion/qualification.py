"""Automated, append-only qualification of public PNW source versions."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import cast
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import APPROVED_SOURCE_HOSTS, Settings
from app.ingestion.discovery import DiscoveredDocument
from app.models.enums import (
    ConflictStatus,
    ExtractionStatus,
    QualificationStatus,
    SourceEventType,
    SourceStatus,
)
from app.models.guidance import Conflict, conflict_evidence_blocks
from app.models.operations import SourceEvent
from app.models.sources import (
    Applicability,
    EvidenceBlock,
    Qualification,
    Source,
    SourceVersion,
)

Clock = Callable[[], datetime]

QUALIFICATION_RULE_VERSION = "pnw-source-qualification-v1"
PNW_INSTITUTION = "Purdue University Northwest"
_DEADLINE_LANGUAGE = re.compile(r"\b(?:deadline|due date|last day)\b", re.IGNORECASE)
_NEGATED_DEADLINE_LANGUAGE = re.compile(
    r"\b(?:no|not|without)\b[^.!?\n]{0,80}\b(?:deadline|due date|last day)\b",
    re.IGNORECASE,
)
_DEADLINE_KEYS = frozenset({"deadline", "due_date"})


class QualificationFailureReason(StrEnum):
    """Bounded reasons why a source version cannot become eligible."""

    SOURCE_WITHDRAWN = "source_withdrawn"
    PROVENANCE_OUTSIDE_BOUNDARY = "provenance_outside_boundary"
    PROVENANCE_MISMATCH = "provenance_mismatch"
    CONTENT_HASH_MISMATCH = "content_hash_mismatch"
    EXTRACTION_INCOMPLETE = "extraction_incomplete"
    EVIDENCE_MISSING = "evidence_missing"
    EVIDENCE_INCOMPLETE = "evidence_incomplete"
    APPLICABILITY_MISSING = "applicability_missing"
    APPLICABILITY_UNSUPPORTED = "applicability_unsupported"
    DEADLINE_CONTEXT_MISSING = "deadline_context_missing"
    EFFECTIVE_PERIOD_INVALID = "effective_period_invalid"
    EFFECTIVE_NOT_STARTED = "effective_not_started"
    EFFECTIVE_EXPIRED = "effective_expired"
    SOURCE_OBSERVATION_STALE = "source_observation_stale"
    SOURCE_OBSERVATION_FUTURE = "source_observation_future"
    UNRESOLVED_CONFLICT = "unresolved_conflict"


class QualificationError(RuntimeError):
    """Base error for sanitized qualification failures."""


class QualificationInputError(QualificationError):
    """The qualification inputs do not identify one persisted source version."""


class QualificationPersistenceError(QualificationError):
    """Qualification state could not be loaded or persisted."""


class ImmutableQualificationError(QualificationError):
    """An operation attempted to change append-only qualification history."""


@dataclass(frozen=True, slots=True)
class QualificationPolicy:
    """Versioned source boundary and freshness rules used for one qualification."""

    allowed_hosts: tuple[str, ...] = tuple(sorted(APPROVED_SOURCE_HOSTS))
    max_observation_age: timedelta = timedelta(hours=24)
    rule_version: str = QUALIFICATION_RULE_VERSION

    def __post_init__(self) -> None:
        hosts = tuple(host.strip().lower().rstrip(".") for host in self.allowed_hosts)
        if not hosts or len(hosts) != len(set(hosts)):
            raise QualificationInputError("qualification hosts must be unique and nonempty")
        if set(hosts) - APPROVED_SOURCE_HOSTS:
            raise QualificationInputError("qualification hosts exceed the approved PNW boundary")
        if self.max_observation_age != timedelta(hours=24):
            raise QualificationInputError("qualification freshness must be exactly 24 hours")
        rule_version = self.rule_version.strip()
        if not rule_version or len(rule_version) > 128:
            raise QualificationInputError("qualification rule version is invalid")
        object.__setattr__(self, "allowed_hosts", hosts)
        object.__setattr__(self, "rule_version", rule_version)

    @classmethod
    def from_settings(cls, settings: Settings) -> QualificationPolicy:
        """Create policy from already validated application settings."""

        return cls(
            allowed_hosts=settings.source_allowed_hosts,
            max_observation_age=settings.qualification_max_age,
        )


@dataclass(frozen=True, slots=True)
class QualificationEvidence:
    """Fresh public fetch that independently proves the version being checked."""

    document: DiscoveredDocument
    observed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.document, DiscoveredDocument):
            raise QualificationInputError("a discovered document is required")
        object.__setattr__(self, "observed_at", _aware_utc(self.observed_at, "observation"))


@dataclass(frozen=True, slots=True)
class QualificationResult:
    """Persisted qualification and the bounded eligibility decision."""

    qualification: Qualification
    eligible: bool
    reason_codes: tuple[QualificationFailureReason, ...]


@dataclass(frozen=True, slots=True)
class _Check:
    passed: bool
    reasons: tuple[QualificationFailureReason, ...] = ()

    def as_json(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "reason_codes": [reason.value for reason in self.reasons],
        }


class SourceQualificationService:
    """Evaluate and persist qualification without committing the caller's transaction."""

    def __init__(
        self,
        session: Session,
        *,
        policy: QualificationPolicy | None = None,
        clock: Clock | None = None,
    ) -> None:
        if not isinstance(session, Session):
            raise TypeError("session must be a SQLAlchemy Session")
        self._session = session
        self._policy = policy or QualificationPolicy()
        self._clock = clock or _utc_now

    def qualify(
        self,
        *,
        source: Source,
        version: SourceVersion,
        evidence: QualificationEvidence,
    ) -> QualificationResult:
        """Record all six checks and update source eligibility atomically on flush."""

        checked_at = _aware_utc(self._clock(), "qualification clock")
        _validate_identity(source, version)
        blocks, applicability, conflicts = self._load_supporting_records(version.id)

        checks = {
            "provenance": self._check_provenance(source, version, evidence),
            "completeness": _check_completeness(version, blocks),
            "applicability": _check_applicability(version, blocks, applicability),
            "effective_date": _check_effective_date(version, checked_at),
            "freshness": self._check_freshness(evidence.observed_at, checked_at),
            "conflict": _check_conflicts(conflicts),
        }
        if source.status is SourceStatus.WITHDRAWN:
            checks["provenance"] = _with_reason(
                checks["provenance"], QualificationFailureReason.SOURCE_WITHDRAWN
            )

        reasons = tuple(
            dict.fromkeys(reason for check in checks.values() for reason in check.reasons)
        )
        eligible = not reasons
        valid_until = checked_at
        if eligible:
            valid_until = checked_at + self._policy.max_observation_age
            if version.effective_to is not None:
                valid_until = min(valid_until, _aware_utc(version.effective_to, "effective end"))

        qualification = Qualification(
            version_id=version.id,
            rule_version=self._policy.rule_version,
            checked_at=checked_at,
            valid_until=valid_until,
            status=QualificationStatus.PASSED if eligible else QualificationStatus.FAILED,
            check_results={name: check.as_json() for name, check in checks.items()},
            provenance_evidence=_provenance_json(source, version, evidence),
            applicability_evidence=_applicability_json(blocks, applicability, conflicts),
        )
        self._session.add(qualification)

        event_reasons: Sequence[str]
        if eligible:
            source.status = SourceStatus.ELIGIBLE
            event_reasons = ("qualification_passed",)
        else:
            if source.status is not SourceStatus.WITHDRAWN:
                source.status = _failed_source_status(reasons)
            event_reasons = tuple(reason.value for reason in reasons)
        evidence_ids = [block.id for block in blocks]
        for reason_code in event_reasons:
            self._session.add(
                SourceEvent(
                    source_id=source.id,
                    event_type=SourceEventType.QUALIFICATION,
                    timestamp=checked_at,
                    rule_version=self._policy.rule_version,
                    reason_code=reason_code,
                    evidence_ids=evidence_ids,
                )
            )
        self._flush()
        return QualificationResult(
            qualification=qualification,
            eligible=eligible,
            reason_codes=reasons,
        )

    def _load_supporting_records(
        self, version_id: UUID
    ) -> tuple[tuple[EvidenceBlock, ...], tuple[Applicability, ...], tuple[Conflict, ...]]:
        try:
            blocks = tuple(
                self._session.scalars(
                    select(EvidenceBlock)
                    .where(EvidenceBlock.version_id == version_id)
                    .order_by(EvidenceBlock.ordinal)
                ).all()
            )
            applicability = tuple(
                self._session.scalars(
                    select(Applicability)
                    .where(Applicability.version_id == version_id)
                    .order_by(Applicability.topic)
                ).all()
            )
            conflicts = tuple(
                self._session.scalars(
                    select(Conflict)
                    .join(
                        conflict_evidence_blocks,
                        Conflict.id == conflict_evidence_blocks.c.conflict_id,
                    )
                    .join(
                        EvidenceBlock,
                        EvidenceBlock.id == conflict_evidence_blocks.c.evidence_block_id,
                    )
                    .where(
                        EvidenceBlock.version_id == version_id,
                        Conflict.status == ConflictStatus.UNRESOLVED,
                    )
                    .distinct()
                )
                .unique()
                .all()
            )
        except SQLAlchemyError:
            raise QualificationPersistenceError(
                "qualification supporting records could not be loaded"
            ) from None
        return blocks, applicability, conflicts

    def _check_provenance(
        self,
        source: Source,
        version: SourceVersion,
        evidence: QualificationEvidence,
    ) -> _Check:
        document = evidence.document
        urls = (document.requested_url, *document.redirect_chain, document.canonical_url)
        outside_boundary = any(
            not _is_approved_url(url, allowed_hosts=self._policy.allowed_hosts) for url in urls
        )
        mismatched = (
            source.canonical_url != document.canonical_url
            or source.media_type is not document.media_type
        )
        hash_mismatch = version.content_sha256 != sha256(document.content).hexdigest()
        reasons: list[QualificationFailureReason] = []
        if outside_boundary:
            reasons.append(QualificationFailureReason.PROVENANCE_OUTSIDE_BOUNDARY)
        if mismatched:
            reasons.append(QualificationFailureReason.PROVENANCE_MISMATCH)
        if hash_mismatch:
            reasons.append(QualificationFailureReason.CONTENT_HASH_MISMATCH)
        return _Check(not reasons, tuple(reasons))

    def _check_freshness(self, observed_at: datetime, checked_at: datetime) -> _Check:
        if observed_at > checked_at:
            return _Check(False, (QualificationFailureReason.SOURCE_OBSERVATION_FUTURE,))
        if checked_at - observed_at >= self._policy.max_observation_age:
            return _Check(False, (QualificationFailureReason.SOURCE_OBSERVATION_STALE,))
        return _Check(True)

    def _flush(self) -> None:
        try:
            self._session.flush()
        except ImmutableQualificationError:
            raise
        except SQLAlchemyError:
            raise QualificationPersistenceError("qualification could not be persisted") from None


def _check_completeness(
    version: SourceVersion,
    blocks: Sequence[EvidenceBlock],
) -> _Check:
    reasons: list[QualificationFailureReason] = []
    if version.extraction_status is not ExtractionStatus.COMPLETE:
        reasons.append(QualificationFailureReason.EXTRACTION_INCOMPLETE)
    if not blocks:
        reasons.append(QualificationFailureReason.EVIDENCE_MISSING)
    elif any(
        block.version_id != version.id
        or not isinstance(block.id, UUID)
        or not block.text.strip()
        or not block.topic_key.strip()
        for block in blocks
    ):
        reasons.append(QualificationFailureReason.EVIDENCE_INCOMPLETE)
    return _Check(not reasons, tuple(reasons))


def _check_applicability(
    version: SourceVersion,
    blocks: Sequence[EvidenceBlock],
    records: Sequence[Applicability],
) -> _Check:
    if not records:
        return _Check(False, (QualificationFailureReason.APPLICABILITY_MISSING,))

    blocks_by_id = {block.id: block for block in blocks}
    covered_ids: set[UUID] = set()
    unsupported = False
    deadline_context_missing = False
    for record in records:
        referenced = tuple(record.evidence_block_ids)
        matching_blocks = [blocks_by_id.get(block_id) for block_id in referenced]
        if (
            record.version_id != version.id
            or not record.topic.strip()
            or record.institution.strip() != PNW_INSTITUTION
            or not referenced
            or any(block is None or block.topic_key != record.topic for block in matching_blocks)
        ):
            unsupported = True
            continue
        typed_blocks = cast(list[EvidenceBlock], matching_blocks)
        covered_ids.update(block.id for block in typed_blocks)
        if any(_is_deadline_block(block) for block in typed_blocks) and (
            not _present(record.term) or not _present(record.session)
        ):
            deadline_context_missing = True

    if set(blocks_by_id) != covered_ids:
        unsupported = True
    reasons: list[QualificationFailureReason] = []
    if unsupported:
        reasons.append(QualificationFailureReason.APPLICABILITY_UNSUPPORTED)
    if deadline_context_missing:
        reasons.append(QualificationFailureReason.DEADLINE_CONTEXT_MISSING)
    return _Check(not reasons, tuple(reasons))


def _check_effective_date(version: SourceVersion, checked_at: datetime) -> _Check:
    start = (
        _aware_utc(version.effective_from, "effective start")
        if version.effective_from is not None
        else None
    )
    end = (
        _aware_utc(version.effective_to, "effective end")
        if version.effective_to is not None
        else None
    )
    if start is not None and end is not None and end < start:
        return _Check(False, (QualificationFailureReason.EFFECTIVE_PERIOD_INVALID,))
    if start is not None and checked_at < start:
        return _Check(False, (QualificationFailureReason.EFFECTIVE_NOT_STARTED,))
    if end is not None and checked_at >= end:
        return _Check(False, (QualificationFailureReason.EFFECTIVE_EXPIRED,))
    return _Check(True)


def _check_conflicts(conflicts: Sequence[Conflict]) -> _Check:
    if conflicts:
        return _Check(False, (QualificationFailureReason.UNRESOLVED_CONFLICT,))
    return _Check(True)


def _validate_identity(source: Source, version: SourceVersion) -> None:
    if not isinstance(source, Source) or not isinstance(source.id, UUID):
        raise QualificationInputError("a persisted source is required")
    if not isinstance(version, SourceVersion) or not isinstance(version.id, UUID):
        raise QualificationInputError("a persisted source version is required")
    if version.source_id != source.id:
        raise QualificationInputError("source version belongs to another source")


def _provenance_json(
    source: Source,
    version: SourceVersion,
    evidence: QualificationEvidence,
) -> dict[str, object]:
    return {
        "canonical_url": source.canonical_url,
        "requested_url": evidence.document.requested_url,
        "redirect_chain": list(evidence.document.redirect_chain),
        "content_sha256": version.content_sha256,
        "observed_at": evidence.observed_at.isoformat(),
        "media_type": source.media_type.value,
    }


def _applicability_json(
    blocks: Sequence[EvidenceBlock],
    records: Sequence[Applicability],
    conflicts: Sequence[Conflict],
) -> dict[str, object]:
    return {
        "evidence_block_ids": [str(block.id) for block in blocks],
        "records": [
            {
                "topic": record.topic,
                "institution": record.institution,
                "campus": record.campus,
                "student_level": record.student_level,
                "program": record.program,
                "catalog_year": record.catalog_year,
                "term": record.term,
                "session": record.session,
                "evidence_block_ids": [str(block_id) for block_id in record.evidence_block_ids],
            }
            for record in records
        ],
        "unresolved_conflict_ids": [str(conflict.id) for conflict in conflicts],
    }


def _is_approved_url(url: str, *, allowed_hosts: tuple[str, ...]) -> bool:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    return (
        parts.scheme.lower() == "https"
        and host in allowed_hosts
        and port in (None, 443)
        and parts.username is None
        and parts.password is None
        and not parts.fragment
    )


def _is_deadline_block(block: EvidenceBlock) -> bool:
    deadline_text = _NEGATED_DEADLINE_LANGUAGE.sub("", block.text)
    if "deadline" in block.topic_key.casefold() or _DEADLINE_LANGUAGE.search(deadline_text):
        return True
    return _contains_deadline_key(block.structured_content)


def _contains_deadline_key(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            str(key).casefold().replace("-", "_").replace(" ", "_") in _DEADLINE_KEYS
            or _contains_deadline_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_contains_deadline_key(item) for item in value)
    return False


def _failed_source_status(
    reasons: Sequence[QualificationFailureReason],
) -> SourceStatus:
    if reasons and all(
        reason is QualificationFailureReason.SOURCE_OBSERVATION_STALE for reason in reasons
    ):
        return SourceStatus.STALE
    return SourceStatus.QUARANTINED


def _with_reason(check: _Check, reason: QualificationFailureReason) -> _Check:
    return _Check(False, (*check.reasons, reason))


def _present(value: str | None) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _aware_utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise QualificationInputError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)


@event.listens_for(Session, "before_flush")
def _prevent_qualification_mutation(
    session: Session,
    _flush_context: object,
    _instances: object,
) -> None:
    """Keep qualification history append-only at the ORM boundary."""

    if any(isinstance(item, Qualification) for item in session.deleted):
        raise ImmutableQualificationError("qualifications cannot be deleted")
    if any(
        isinstance(item, Qualification) and session.is_modified(item, include_collections=False)
        for item in session.dirty
    ):
        raise ImmutableQualificationError("qualifications cannot be updated")


__all__ = [
    "PNW_INSTITUTION",
    "QUALIFICATION_RULE_VERSION",
    "ImmutableQualificationError",
    "QualificationError",
    "QualificationEvidence",
    "QualificationFailureReason",
    "QualificationInputError",
    "QualificationPersistenceError",
    "QualificationPolicy",
    "QualificationResult",
    "SourceQualificationService",
]
