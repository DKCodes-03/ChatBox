"""Source-backed scope rules for programs and general academic procedures."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import cast
from uuid import UUID

from app.context import ContextField, ContextValidationError, StudentContext, validate_context

MAX_ACADEMIC_SCOPE_VALUE_CHARACTERS = 255
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


class AcademicTopic(StrEnum):
    PROGRAM = "program"
    GRADUATE_ADMISSION = "graduate_admission"
    PLAN_OF_STUDY = "plan_of_study"
    GRADUATION = "graduation"
    CURRENT_AVAILABILITY = "current_availability"


class ProgramStatus(StrEnum):
    """A status that must be stated explicitly by eligible catalog evidence."""

    EXISTS = "exists"
    NOT_OFFERED = "not_offered"


class AvailabilityBasis(StrEnum):
    """The type of evidence behind a course-availability statement."""

    CURRENT_SCHEDULE = "current_schedule"
    TYPICAL_OFFERING = "typical_offering"
    CATALOG_ENTRY = "catalog_entry"


class AcademicScopeOutcome(StrEnum):
    SUPPORTED = "supported"
    CONTEXT_REQUIRED = "context_required"
    LIMITED = "limited"


class AcademicLimitation(StrEnum):
    MISSING_EVIDENCE = "missing_evidence"
    CONTEXT_UNSUPPORTED = "context_unsupported"
    PERSONAL_DECISION = "personal_decision"
    CURRENT_AVAILABILITY_UNCONFIRMED = "current_availability_unconfirmed"


class InvalidAcademicScopeError(ValueError):
    """Academic evidence metadata is unsafe to apply or summarize."""

    def __init__(self) -> None:
        super().__init__("academic scope data is invalid")


@dataclass(frozen=True, slots=True)
class AcademicEvidenceProfile:
    """Scope and assertion metadata extracted from one eligible evidence block."""

    evidence_id: UUID
    topic: AcademicTopic
    campus: str | None = None
    term: str | None = None
    year: str | None = None
    session: str | None = None
    program: str | None = None
    student_level: str | None = None
    catalog_year: str | None = None
    program_status: ProgramStatus | None = None
    availability_basis: AvailabilityBasis | None = None


@dataclass(frozen=True, slots=True)
class AcademicScopeDecision:
    """What source-backed academic guidance may be given for an explicit context."""

    topic: AcademicTopic
    outcome: AcademicScopeOutcome
    selected_context: StudentContext
    evidence_ids: tuple[UUID, ...] = ()
    missing_fields: tuple[ContextField, ...] = ()
    limitation: AcademicLimitation | None = None
    program_status: ProgramStatus | None = None
    allows_general_guidance: bool = False
    allows_personal_decision: bool = False
    can_confirm_current_availability: bool = False
    can_claim_program_absence: bool = False


_FIELD_ATTRIBUTE = {
    ContextField.CAMPUS: "campus",
    ContextField.TERM: "term",
    ContextField.YEAR: "year",
    ContextField.SESSION: "session",
    ContextField.PROGRAM: "program",
    ContextField.STUDENT_LEVEL: "student_level",
    ContextField.CATALOG_YEAR: "catalog_year",
}

_FIELD_ORDER = {
    AcademicTopic.PROGRAM: (
        ContextField.PROGRAM,
        ContextField.STUDENT_LEVEL,
        ContextField.CATALOG_YEAR,
        ContextField.CAMPUS,
    ),
    AcademicTopic.GRADUATE_ADMISSION: (
        ContextField.STUDENT_LEVEL,
        ContextField.CATALOG_YEAR,
        ContextField.PROGRAM,
        ContextField.CAMPUS,
    ),
    AcademicTopic.PLAN_OF_STUDY: (
        ContextField.PROGRAM,
        ContextField.STUDENT_LEVEL,
        ContextField.CATALOG_YEAR,
        ContextField.CAMPUS,
    ),
    AcademicTopic.GRADUATION: (
        ContextField.PROGRAM,
        ContextField.STUDENT_LEVEL,
        ContextField.CATALOG_YEAR,
        ContextField.CAMPUS,
    ),
    AcademicTopic.CURRENT_AVAILABILITY: (
        ContextField.CAMPUS,
        ContextField.PROGRAM,
        ContextField.TERM,
        ContextField.YEAR,
        ContextField.SESSION,
        ContextField.CATALOG_YEAR,
    ),
}

_BASE_REQUIRED = {
    AcademicTopic.PROGRAM: (ContextField.PROGRAM, ContextField.CATALOG_YEAR),
    AcademicTopic.GRADUATE_ADMISSION: (
        ContextField.STUDENT_LEVEL,
        ContextField.CATALOG_YEAR,
    ),
    AcademicTopic.PLAN_OF_STUDY: (
        ContextField.PROGRAM,
        ContextField.STUDENT_LEVEL,
        ContextField.CATALOG_YEAR,
    ),
    AcademicTopic.GRADUATION: (
        ContextField.PROGRAM,
        ContextField.STUDENT_LEVEL,
        ContextField.CATALOG_YEAR,
    ),
    AcademicTopic.CURRENT_AVAILABILITY: (),
}


def _clean_optional(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidAcademicScopeError()
    cleaned = " ".join(value.split())
    if (
        not cleaned
        or len(cleaned) > MAX_ACADEMIC_SCOPE_VALUE_CHARACTERS
        or _CONTROL_RE.search(value)
    ):
        raise InvalidAcademicScopeError()
    return cleaned


def _enum_value[T: StrEnum](enum_type: type[T], value: object) -> T:
    if not isinstance(value, str):
        raise InvalidAcademicScopeError()
    try:
        return enum_type(value)
    except ValueError:
        raise InvalidAcademicScopeError() from None


def _optional_enum[T: StrEnum](enum_type: type[T], value: object) -> T | None:
    return None if value is None else _enum_value(enum_type, value)


def _normalize_profile(profile: AcademicEvidenceProfile) -> AcademicEvidenceProfile:
    if not isinstance(profile, AcademicEvidenceProfile) or not isinstance(profile.evidence_id, UUID):
        raise InvalidAcademicScopeError()
    normalized = AcademicEvidenceProfile(
        evidence_id=profile.evidence_id,
        topic=_enum_value(AcademicTopic, profile.topic),
        campus=_clean_optional(profile.campus),
        term=_clean_optional(profile.term),
        year=_clean_optional(profile.year),
        session=_clean_optional(profile.session),
        program=_clean_optional(profile.program),
        student_level=_clean_optional(profile.student_level),
        catalog_year=_clean_optional(profile.catalog_year),
        program_status=_optional_enum(ProgramStatus, profile.program_status),
        availability_basis=_optional_enum(AvailabilityBasis, profile.availability_basis),
    )
    _validate_topic_profile(normalized)
    return normalized


def _validate_topic_profile(profile: AcademicEvidenceProfile) -> None:
    if profile.topic is AcademicTopic.PROGRAM:
        if (
            profile.program is None
            or profile.catalog_year is None
            or profile.program_status is None
            or profile.availability_basis is not None
        ):
            raise InvalidAcademicScopeError()
    elif profile.topic is AcademicTopic.GRADUATE_ADMISSION:
        if (
            profile.student_level is None
            or profile.student_level.casefold() != "graduate"
            or profile.catalog_year is None
            or profile.program_status is not None
            or profile.availability_basis is not None
        ):
            raise InvalidAcademicScopeError()
    elif profile.topic in (AcademicTopic.PLAN_OF_STUDY, AcademicTopic.GRADUATION):
        if (
            profile.program is None
            or profile.student_level is None
            or profile.catalog_year is None
            or profile.program_status is not None
            or profile.availability_basis is not None
        ):
            raise InvalidAcademicScopeError()
    elif profile.topic is AcademicTopic.CURRENT_AVAILABILITY:
        if (
            profile.availability_basis is None
            or profile.program_status is not None
            or (
                profile.availability_basis is AvailabilityBasis.CURRENT_SCHEDULE
                and (profile.term is None or profile.year is None)
            )
        ):
            raise InvalidAcademicScopeError()
    else:
        raise InvalidAcademicScopeError()


def _profile_value(profile: AcademicEvidenceProfile, field: ContextField) -> str | None:
    return cast(str | None, getattr(profile, _FIELD_ATTRIBUTE[field]))


def _matches(profile: AcademicEvidenceProfile, context: StudentContext) -> bool:
    explicit = context.as_field_mapping()
    return all(
        explicit.get(field) is None
        or _profile_value(profile, field) is None
        or explicit[field] == _profile_value(profile, field)
        for field in ContextField
    )


def _required_fields(
    topic: AcademicTopic,
    profiles: Sequence[AcademicEvidenceProfile],
) -> tuple[ContextField, ...]:
    required = set(_BASE_REQUIRED[topic])
    for field in _FIELD_ORDER[topic]:
        if any(_profile_value(profile, field) is not None for profile in profiles):
            required.add(field)
    if topic is AcademicTopic.CURRENT_AVAILABILITY and any(
        profile.availability_basis is AvailabilityBasis.CURRENT_SCHEDULE for profile in profiles
    ):
        required.update((ContextField.TERM, ContextField.YEAR))
    return tuple(field for field in _FIELD_ORDER[topic] if field in required)


def _selected_context(
    context: StudentContext,
    fields: Iterable[ContextField],
) -> StudentContext:
    explicit = context.as_field_mapping()
    return StudentContext.model_validate(
        {field.value: explicit[field] for field in fields if field in explicit}
    )


def _evidence_ids(profiles: Iterable[AcademicEvidenceProfile]) -> tuple[UUID, ...]:
    return tuple(sorted({profile.evidence_id for profile in profiles}, key=str))


class AcademicScopeRules:
    """Apply academic evidence only within explicit, source-backed scope."""

    def evaluate(
        self,
        *,
        topic: AcademicTopic,
        context: StudentContext | Mapping[str, object] | None,
        profiles: Sequence[AcademicEvidenceProfile],
        requests_personal_decision: bool = False,
    ) -> AcademicScopeDecision:
        checked_topic = _enum_value(AcademicTopic, topic)
        if not isinstance(profiles, Sequence) or isinstance(profiles, (str, bytes)):
            raise InvalidAcademicScopeError()
        if not isinstance(requests_personal_decision, bool):
            raise InvalidAcademicScopeError()
        try:
            explicit = validate_context(context)
        except ContextValidationError:
            raise InvalidAcademicScopeError() from None

        normalized = tuple(_normalize_profile(profile) for profile in profiles)
        if len({(profile.evidence_id, profile.topic) for profile in normalized}) != len(normalized):
            raise InvalidAcademicScopeError()
        topic_profiles = tuple(profile for profile in normalized if profile.topic is checked_topic)
        if not topic_profiles:
            return AcademicScopeDecision(
                topic=checked_topic,
                outcome=AcademicScopeOutcome.LIMITED,
                selected_context=StudentContext(),
                limitation=AcademicLimitation.MISSING_EVIDENCE,
            )

        matching = tuple(profile for profile in topic_profiles if _matches(profile, explicit))
        relevant_fields = _required_fields(checked_topic, topic_profiles)
        selected = _selected_context(explicit, relevant_fields)
        if not matching:
            return AcademicScopeDecision(
                topic=checked_topic,
                outcome=AcademicScopeOutcome.LIMITED,
                selected_context=selected,
                limitation=AcademicLimitation.CONTEXT_UNSUPPORTED,
            )

        required = _required_fields(checked_topic, matching)
        missing = selected.missing(required)
        if missing:
            return AcademicScopeDecision(
                topic=checked_topic,
                outcome=AcademicScopeOutcome.CONTEXT_REQUIRED,
                selected_context=selected,
                missing_fields=missing,
            )

        selected = _selected_context(explicit, required)
        evidence_ids = _evidence_ids(matching)
        if requests_personal_decision:
            return AcademicScopeDecision(
                topic=checked_topic,
                outcome=AcademicScopeOutcome.LIMITED,
                selected_context=selected,
                evidence_ids=evidence_ids,
                limitation=AcademicLimitation.PERSONAL_DECISION,
                allows_general_guidance=True,
            )

        if checked_topic is AcademicTopic.PROGRAM:
            statuses = {profile.program_status for profile in matching}
            if len(statuses) != 1 or None in statuses:
                raise InvalidAcademicScopeError()
            status = cast(ProgramStatus, next(iter(statuses)))
            return AcademicScopeDecision(
                topic=checked_topic,
                outcome=AcademicScopeOutcome.SUPPORTED,
                selected_context=selected,
                evidence_ids=evidence_ids,
                program_status=status,
                allows_general_guidance=True,
                can_claim_program_absence=status is ProgramStatus.NOT_OFFERED,
            )

        if checked_topic is AcademicTopic.CURRENT_AVAILABILITY:
            current = tuple(
                profile
                for profile in matching
                if profile.availability_basis is AvailabilityBasis.CURRENT_SCHEDULE
            )
            if not current:
                return AcademicScopeDecision(
                    topic=checked_topic,
                    outcome=AcademicScopeOutcome.LIMITED,
                    selected_context=selected,
                    evidence_ids=evidence_ids,
                    limitation=AcademicLimitation.CURRENT_AVAILABILITY_UNCONFIRMED,
                    allows_general_guidance=True,
                )
            return AcademicScopeDecision(
                topic=checked_topic,
                outcome=AcademicScopeOutcome.SUPPORTED,
                selected_context=selected,
                evidence_ids=_evidence_ids(current),
                allows_general_guidance=True,
                can_confirm_current_availability=True,
            )

        return AcademicScopeDecision(
            topic=checked_topic,
            outcome=AcademicScopeOutcome.SUPPORTED,
            selected_context=selected,
            evidence_ids=evidence_ids,
            allows_general_guidance=True,
        )
