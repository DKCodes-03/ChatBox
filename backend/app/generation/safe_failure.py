"""Deterministic safe limitations and evidence-backed office referrals."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Protocol
from urllib.parse import urlsplit
from uuid import UUID

from app.generation.schemas import (
    AnswerOutcome,
    AnswerSegment,
    ReasonCode,
    SegmentKind,
    StructuredAnswer,
)
from app.generation.validator import EvidenceCitation, citation_from_evidence
from app.retrieval.search import RetrievalScope, RetrievedEvidence

MAX_CITATIONS = 8
MAX_CONTACT_URL_CHARACTERS = 2_048
MAX_OFFICE_NAME_CHARACTERS = 255
MAX_CONTACT_LABEL_CHARACTERS = 255
MAX_RESPONSIBILITIES_CHARACTERS = 4_000

NO_VERIFIED_REFERRAL_LIMITATION = (
    "I do not have verified university contact information for this question."
)

LIMITATIONS_BY_REASON: Mapping[ReasonCode, str] = MappingProxyType(
    {
        ReasonCode.MISSING_EVIDENCE: (
            "I cannot provide a reliable answer from the available university information."
        ),
        ReasonCode.CONFLICT: (
            "I cannot provide a definitive answer because the available university information "
            "conflicts."
        ),
        ReasonCode.EXPIRED_SOURCE: (
            "I cannot provide a reliable answer because the relevant university information is "
            "no longer current."
        ),
        ReasonCode.CONTEXT_REQUIRED: (
            "I cannot provide a reliable answer until the required context is known."
        ),
        ReasonCode.PERSONAL_CASE: (
            "I cannot access student records or determine the result of a personal case."
        ),
        ReasonCode.OUT_OF_SCOPE: (
            "I cannot provide a reliable answer because this question is outside the supported "
            "university topics."
        ),
        ReasonCode.SOURCE_UNAVAILABLE: (
            "I cannot provide a reliable answer because the relevant university source is unavailable."
        ),
        ReasonCode.PROCESSING_UNAVAILABLE: (
            "I cannot provide a reliable answer right now because the answer service is temporarily "
            "unavailable. Please try again later."
        ),
    }
)

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TELEPHONE_RE = re.compile(r"^\+?[0-9(). -]{7,32}$")
_REFERRAL_SCOPE_FIELDS = frozenset(
    {
        "topic",
        "institution",
        "campus",
        "student_level",
        "program",
        "catalog_year",
        "term",
        "session",
        "year",
    }
)


class OfficeReferralRecord(Protocol):
    """Minimum persisted referral fields required by the assembler."""

    @property
    def office_name(self) -> str: ...

    @property
    def responsibilities(self) -> str: ...

    @property
    def contact_label(self) -> str: ...

    @property
    def contact_url(self) -> str: ...

    @property
    def evidence_block_id(self) -> UUID: ...

    @property
    def scope(self) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class VerifiedOfficeReferral:
    """Public referral fields plus the eligible evidence that supports them."""

    office_name: str
    contact_label: str
    contact_url: str
    evidence_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class SafeFailureResult:
    """A fixed unable answer and optional evidence-backed referral information."""

    answer: StructuredAnswer
    referral: VerifiedOfficeReferral | None
    citations: tuple[EvidenceCitation, ...]
    related_citation_ids: tuple[UUID, ...]
    citation_evidence_ids: tuple[UUID, ...]


def assemble_safe_failure(
    reason_code: ReasonCode,
    *,
    evidence: Sequence[RetrievedEvidence],
    scope: RetrievalScope,
    referral_candidates: Sequence[OfficeReferralRecord] = (),
    related_evidence_ids: Sequence[UUID] = (),
) -> SafeFailureResult:
    """Build an unable answer without using generated claims or unverified contacts.

    ``evidence`` must come from the eligibility-gated retriever or its qualified-source conflict
    diagnostic. The assembler checks exact scope and public citation metadata before using it.
    Conflicting-source IDs are exposed separately so the API layer can cite those sources without
    treating the system-authored limitation as a claim extracted from one source.
    """

    if not isinstance(reason_code, ReasonCode):
        raise TypeError("reason_code must be a reason code")
    if not isinstance(scope, RetrievalScope):
        raise TypeError("scope must be a retrieval scope")

    eligible_evidence: dict[UUID, RetrievedEvidence] = {}
    citation_by_evidence_id: dict[UUID, EvidenceCitation] = {}
    for item in evidence:
        if not isinstance(item, RetrievedEvidence) or not _scope_matches(item, scope):
            continue
        citation = citation_from_evidence(item)
        if citation is None:
            continue
        eligible_evidence[item.evidence_id] = item
        citation_by_evidence_id[item.evidence_id] = citation
    referral = _verified_referral(
        referral_candidates,
        eligible_evidence=eligible_evidence,
        scope=scope,
    )

    limitation_segments = [
        AnswerSegment(
            kind=SegmentKind.LIMITATION,
            text=LIMITATIONS_BY_REASON[reason_code],
            evidence_ids=(),
        )
    ]
    if referral is None:
        limitation_segments.append(
            AnswerSegment(
                kind=SegmentKind.LIMITATION,
                text=NO_VERIFIED_REFERRAL_LIMITATION,
                evidence_ids=(),
            )
        )

    related_citation_ids: list[UUID] = []
    if reason_code is ReasonCode.CONFLICT:
        related_citation_ids.extend(
            evidence_id
            for evidence_id in related_evidence_ids
            if isinstance(evidence_id, UUID) and evidence_id in eligible_evidence
        )

    citation_ids = list(referral.evidence_ids if referral is not None else ())
    citation_ids.extend(related_citation_ids)
    bounded_citation_ids = tuple(dict.fromkeys(citation_ids))[:MAX_CITATIONS]
    bounded_related_ids = tuple(
        evidence_id
        for evidence_id in dict.fromkeys(related_citation_ids)
        if evidence_id in bounded_citation_ids
    )

    return SafeFailureResult(
        answer=StructuredAnswer(
            outcome=AnswerOutcome.UNABLE,
            segments=tuple(limitation_segments),
            clarification=None,
            reason_code=reason_code,
        ),
        referral=referral,
        citations=tuple(
            citation_by_evidence_id[evidence_id] for evidence_id in bounded_citation_ids
        ),
        related_citation_ids=bounded_related_ids,
        citation_evidence_ids=bounded_citation_ids,
    )


def _verified_referral(
    candidates: Sequence[OfficeReferralRecord],
    *,
    eligible_evidence: Mapping[UUID, RetrievedEvidence],
    scope: RetrievalScope,
) -> VerifiedOfficeReferral | None:
    grouped: dict[tuple[str, str, str], list[UUID]] = {}
    for candidate in candidates:
        normalized = _normalize_candidate(
            candidate, eligible_evidence=eligible_evidence, scope=scope
        )
        if normalized is None:
            continue
        key, evidence_id = normalized
        evidence_ids = grouped.setdefault(key, [])
        if evidence_id not in evidence_ids:
            evidence_ids.append(evidence_id)

    # Choosing among different offices or contact routes would be an unsupported recommendation.
    if len(grouped) != 1:
        return None
    (office_name, contact_label, contact_url), evidence_ids = next(iter(grouped.items()))
    return VerifiedOfficeReferral(
        office_name=office_name,
        contact_label=contact_label,
        contact_url=contact_url,
        evidence_ids=tuple(evidence_ids[:MAX_CITATIONS]),
    )


def _normalize_candidate(
    candidate: OfficeReferralRecord,
    *,
    eligible_evidence: Mapping[UUID, RetrievedEvidence],
    scope: RetrievalScope,
) -> tuple[tuple[str, str, str], UUID] | None:
    try:
        evidence_id = candidate.evidence_block_id
        referral_scope = candidate.scope
        office_name = _clean_text(candidate.office_name, maximum=MAX_OFFICE_NAME_CHARACTERS)
        responsibilities = _clean_text(
            candidate.responsibilities,
            maximum=MAX_RESPONSIBILITIES_CHARACTERS,
        )
        contact_label = _clean_text(
            candidate.contact_label,
            maximum=MAX_CONTACT_LABEL_CHARACTERS,
        )
        contact_url = _contact_url(candidate.contact_url)
    except (AttributeError, TypeError, ValueError):
        return None

    if (
        not isinstance(evidence_id, UUID)
        or evidence_id not in eligible_evidence
        or office_name is None
        or responsibilities is None
        or contact_label is None
        or contact_url is None
        or not _referral_scope_matches(referral_scope, scope)
    ):
        return None
    return (office_name, contact_label, contact_url), evidence_id


def _clean_text(value: object, *, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned or len(cleaned) > maximum or _CONTROL_RE.search(cleaned):
        return None
    return cleaned


def _contact_url(value: object) -> str | None:
    cleaned = _clean_text(value, maximum=MAX_CONTACT_URL_CHARACTERS)
    if cleaned is None or any(character.isspace() for character in cleaned):
        return None
    parsed = urlsplit(cleaned)
    if parsed.username is not None or parsed.password is not None:
        return None
    if parsed.scheme == "https":
        return cleaned if parsed.hostname else None
    if parsed.scheme == "mailto":
        return (
            cleaned
            if not parsed.query and not parsed.fragment and _EMAIL_RE.fullmatch(parsed.path)
            else None
        )
    if parsed.scheme == "tel":
        return (
            cleaned
            if not parsed.query and not parsed.fragment and _TELEPHONE_RE.fullmatch(parsed.path)
            else None
        )
    return None


def _referral_scope_matches(candidate_scope: object, scope: RetrievalScope) -> bool:
    if not isinstance(candidate_scope, Mapping):
        return False
    if any(
        not isinstance(key, str) or key not in _REFERRAL_SCOPE_FIELDS for key in candidate_scope
    ):
        return False
    expected: Mapping[str, str | None] = {
        "topic": scope.topic,
        "institution": scope.institution,
        "campus": scope.campus,
        "student_level": scope.student_level,
        "program": scope.program,
        "catalog_year": scope.catalog_year,
        "term": scope.term,
        "session": scope.session,
        "year": scope.year,
    }
    for key, value in candidate_scope.items():
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip() or value.strip() != expected[key]:
            return False
    return True


def _scope_matches(evidence: RetrievedEvidence, scope: RetrievalScope) -> bool:
    applicable = evidence.applicability
    return (
        applicable.topic == scope.topic
        and evidence.topic_key == scope.topic
        and applicable.institution == scope.institution
        and applicable.campus == scope.campus
        and applicable.student_level == scope.student_level
        and applicable.program == scope.program
        and applicable.catalog_year == scope.catalog_year
        and applicable.term == scope.term
        and applicable.session == scope.session
        and applicable.year == scope.year
    )


__all__ = [
    "LIMITATIONS_BY_REASON",
    "NO_VERIFIED_REFERRAL_LIMITATION",
    "OfficeReferralRecord",
    "SafeFailureResult",
    "VerifiedOfficeReferral",
    "assemble_safe_failure",
]
