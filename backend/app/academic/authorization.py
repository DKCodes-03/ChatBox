"""Authorize academic guidance from structured, eligible retrieval results."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from sqlalchemy.orm import Session

from app.academic.prerequisites import CoursePrerequisites, PrerequisiteRepository
from app.academic.programs import (
    AcademicEvidenceProfile,
    AcademicScopeDecision,
    AcademicScopeRules,
    AcademicTopic,
    AvailabilityBasis,
    ProgramStatus,
)
from app.context import StudentContext
from app.generation.schemas import ReasonCode
from app.retrieval.search import RetrievedEvidence

_PERSONAL_DECISION_RE = re.compile(
    r"\b(?:am|can|may|will|did|have)\s+i\b.{0,100}"
    r"\b(?:eligible|allowed|admitted|graduate|graduating|completed|take|register|approved|valid)\b"
    r"|\bmy\b.{0,80}\b(?:eligibility|admission decision|graduation status|degree audit|plan is valid)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class AcademicAuthorization:
    """Academic evidence authorized for generation, or a reason to stop safely."""

    decision: AcademicScopeDecision | None = None
    prerequisites: tuple[CoursePrerequisites, ...] = ()
    failure_reason: ReasonCode | None = None


def authorize_academic_evidence(
    session: Session,
    *,
    question: str,
    context: StudentContext,
    evidence: Sequence[RetrievedEvidence],
    rules: AcademicScopeRules | None = None,
) -> AcademicAuthorization:
    """Apply US4 scope and relationship checks to eligible retrieved evidence.

    The generic retriever has already applied source qualification and exact applicability. This
    additional gate ensures academic assertions use their structured meaning rather than asking an
    LLM to infer program status, prerequisite logic, or current availability from prose alone.
    """

    academic_profiles: list[AcademicEvidenceProfile] = []
    academic_topics: set[AcademicTopic] = set()
    prerequisite_summaries: list[CoursePrerequisites] = []
    repository = PrerequisiteRepository(session)
    personal_decision = _requests_personal_decision(question)

    for item in evidence:
        content = _structured_content(item)
        if content is None:
            continue

        if content.get("type") == "course_requirements":
            if personal_decision:
                return AcademicAuthorization(failure_reason=ReasonCode.PERSONAL_CASE)
            course_code = content.get("course_code")
            if not isinstance(course_code, str):
                return AcademicAuthorization(failure_reason=ReasonCode.MISSING_EVIDENCE)
            summaries = repository.retrieve(
                course_code=course_code,
                eligible_evidence_ids=(item.evidence_id,),
                campus=item.applicability.campus,
                catalog_year=item.applicability.catalog_year,
            )
            if not summaries:
                return AcademicAuthorization(failure_reason=ReasonCode.MISSING_EVIDENCE)
            prerequisite_summaries.extend(summaries)

        topic = _academic_topic(item, content)
        if topic is None:
            continue
        academic_topics.add(topic)
        academic_profiles.append(_academic_profile(item, content, topic))

    if len(academic_topics) > 1:
        return AcademicAuthorization(failure_reason=ReasonCode.MISSING_EVIDENCE)
    if not academic_topics:
        return AcademicAuthorization(prerequisites=tuple(prerequisite_summaries))

    topic = next(iter(academic_topics))
    decision = (rules or AcademicScopeRules()).evaluate(
        topic=topic,
        context=context,
        profiles=tuple(academic_profiles),
        requests_personal_decision=personal_decision,
    )
    return AcademicAuthorization(
        decision=decision,
        prerequisites=tuple(prerequisite_summaries),
    )


def _requests_personal_decision(question: object) -> bool:
    return isinstance(question, str) and _PERSONAL_DECISION_RE.search(question) is not None


def _structured_content(item: RetrievedEvidence) -> Mapping[str, object] | None:
    content = item.structured_content
    if not isinstance(content, Mapping):
        return None
    return cast(Mapping[str, object], content)


def _academic_topic(
    item: RetrievedEvidence,
    content: Mapping[str, object],
) -> AcademicTopic | None:
    content_type = content.get("type")
    topic_key = item.topic_key.casefold().replace("-", "_")
    if content_type == "program_catalog_entry":
        return AcademicTopic.PROGRAM
    if content_type in {"typical_offering", "current_schedule"}:
        return AcademicTopic.CURRENT_AVAILABILITY
    if content_type != "ordered_procedure":
        return None
    if "graduate_admission" in topic_key:
        return AcademicTopic.GRADUATE_ADMISSION
    if "plan_of_study" in topic_key:
        return AcademicTopic.PLAN_OF_STUDY
    if "graduation" in topic_key:
        return AcademicTopic.GRADUATION
    return None


def _academic_profile(
    item: RetrievedEvidence,
    content: Mapping[str, object],
    topic: AcademicTopic,
) -> AcademicEvidenceProfile:
    applicability = item.applicability
    year = item.scope.get("year")
    return AcademicEvidenceProfile(
        evidence_id=item.evidence_id,
        topic=topic,
        campus=applicability.campus,
        term=applicability.term,
        year=year if isinstance(year, str) else None,
        session=applicability.session,
        program=applicability.program,
        student_level=applicability.student_level,
        catalog_year=applicability.catalog_year,
        program_status=(
            ProgramStatus(cast(str, content["program_status"]))
            if topic is AcademicTopic.PROGRAM and isinstance(content.get("program_status"), str)
            else None
        ),
        availability_basis=(
            AvailabilityBasis(cast(str, content["availability_basis"]))
            if topic is AcademicTopic.CURRENT_AVAILABILITY
            and isinstance(content.get("availability_basis"), str)
            else None
        ),
    )


__all__ = ["AcademicAuthorization", "authorize_academic_evidence"]
