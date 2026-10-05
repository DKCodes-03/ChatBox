"""Unit coverage for academic-program and procedure scope rules."""

from __future__ import annotations

from uuid import UUID

import pytest
from app.academic.programs import (
    AcademicEvidenceProfile,
    AcademicLimitation,
    AcademicScopeOutcome,
    AcademicScopeRules,
    AcademicTopic,
    AvailabilityBasis,
    InvalidAcademicScopeError,
    ProgramStatus,
)
from app.context import ContextField, StudentContext


def _id(number: int) -> UUID:
    return UUID(f"61000000-0000-4000-8000-{number:012d}")


def test_program_status_requires_explicit_source_status_and_catalog_context() -> None:
    profile = AcademicEvidenceProfile(
        evidence_id=_id(1),
        topic=AcademicTopic.PROGRAM,
        program="Computer Science, PhD",
        student_level="Graduate",
        catalog_year="2030-2031",
        program_status=ProgramStatus.EXISTS,
    )
    rules = AcademicScopeRules()

    missing = rules.evaluate(topic=AcademicTopic.PROGRAM, context={}, profiles=(profile,))
    supported = rules.evaluate(
        topic=AcademicTopic.PROGRAM,
        context={
            "program": "Computer Science, PhD",
            "student_level": "Graduate",
            "catalog_year": "2030-2031",
        },
        profiles=(profile,),
    )

    assert missing.outcome is AcademicScopeOutcome.CONTEXT_REQUIRED
    assert missing.missing_fields == (
        ContextField.PROGRAM,
        ContextField.STUDENT_LEVEL,
        ContextField.CATALOG_YEAR,
    )
    assert supported.outcome is AcademicScopeOutcome.SUPPORTED
    assert supported.evidence_ids == (_id(1),)
    assert supported.program_status is ProgramStatus.EXISTS
    assert supported.can_claim_program_absence is False


def test_missing_program_evidence_never_becomes_a_nonexistence_claim() -> None:
    decision = AcademicScopeRules().evaluate(
        topic=AcademicTopic.PROGRAM,
        context={"program": "Missing Program", "catalog_year": "2030-2031"},
        profiles=(),
    )

    assert decision.outcome is AcademicScopeOutcome.LIMITED
    assert decision.limitation is AcademicLimitation.MISSING_EVIDENCE
    assert decision.program_status is None
    assert decision.can_claim_program_absence is False


def test_explicit_not_offered_status_can_support_a_scoped_negative_answer() -> None:
    profile = AcademicEvidenceProfile(
        evidence_id=_id(2),
        topic=AcademicTopic.PROGRAM,
        program="Synthetic Robotics, PhD",
        catalog_year="2030-2031",
        program_status=ProgramStatus.NOT_OFFERED,
    )

    decision = AcademicScopeRules().evaluate(
        topic=AcademicTopic.PROGRAM,
        context={"program": "Synthetic Robotics, PhD", "catalog_year": "2030-2031"},
        profiles=(profile,),
    )

    assert decision.outcome is AcademicScopeOutcome.SUPPORTED
    assert decision.program_status is ProgramStatus.NOT_OFFERED
    assert decision.can_claim_program_absence is True


def test_graduate_admission_requires_graduate_audience_and_catalog_scope() -> None:
    profile = AcademicEvidenceProfile(
        evidence_id=_id(3),
        topic=AcademicTopic.GRADUATE_ADMISSION,
        student_level="Graduate",
        catalog_year="2030-2031",
    )
    decision = AcademicScopeRules().evaluate(
        topic=AcademicTopic.GRADUATE_ADMISSION,
        context={"student_level": "Graduate", "catalog_year": "2030-2031"},
        profiles=(profile,),
    )

    assert decision.outcome is AcademicScopeOutcome.SUPPORTED
    assert decision.evidence_ids == (_id(3),)
    assert decision.selected_context == StudentContext(
        student_level="Graduate", catalog_year="2030-2031"
    )

    with pytest.raises(InvalidAcademicScopeError):
        AcademicScopeRules().evaluate(
            topic=AcademicTopic.GRADUATE_ADMISSION,
            context={"student_level": "Undergraduate", "catalog_year": "2030-2031"},
            profiles=(
                AcademicEvidenceProfile(
                    evidence_id=_id(4),
                    topic=AcademicTopic.GRADUATE_ADMISSION,
                    student_level="Undergraduate",
                    catalog_year="2030-2031",
                ),
            ),
        )


@pytest.mark.parametrize("topic", [AcademicTopic.PLAN_OF_STUDY, AcademicTopic.GRADUATION])
def test_plan_and_graduation_allow_general_steps_but_never_personal_decisions(
    topic: AcademicTopic,
) -> None:
    profile = AcademicEvidenceProfile(
        evidence_id=_id(5),
        topic=topic,
        program="Computer Science, MS",
        student_level="Graduate",
        catalog_year="2030-2031",
    )
    context = {
        "program": "Computer Science, MS",
        "student_level": "Graduate",
        "catalog_year": "2030-2031",
    }
    rules = AcademicScopeRules()

    general = rules.evaluate(topic=topic, context=context, profiles=(profile,))
    personal = rules.evaluate(
        topic=topic,
        context=context,
        profiles=(profile,),
        requests_personal_decision=True,
    )

    assert general.outcome is AcademicScopeOutcome.SUPPORTED
    assert general.allows_general_guidance is True
    assert general.allows_personal_decision is False
    assert personal.outcome is AcademicScopeOutcome.LIMITED
    assert personal.limitation is AcademicLimitation.PERSONAL_DECISION
    assert personal.evidence_ids == (_id(5),)
    assert personal.allows_general_guidance is True
    assert personal.allows_personal_decision is False


def test_typical_offering_does_not_confirm_current_availability() -> None:
    typical = AcademicEvidenceProfile(
        evidence_id=_id(6),
        topic=AcademicTopic.CURRENT_AVAILABILITY,
        campus="Hammond",
        term="Fall",
        year="2030",
        availability_basis=AvailabilityBasis.TYPICAL_OFFERING,
    )

    decision = AcademicScopeRules().evaluate(
        topic=AcademicTopic.CURRENT_AVAILABILITY,
        context={"campus": "Hammond", "term": "Fall", "year": "2030"},
        profiles=(typical,),
    )

    assert decision.outcome is AcademicScopeOutcome.LIMITED
    assert decision.limitation is AcademicLimitation.CURRENT_AVAILABILITY_UNCONFIRMED
    assert decision.can_confirm_current_availability is False
    assert decision.evidence_ids == (_id(6),)


def test_current_schedule_can_confirm_availability_only_for_its_exact_scope() -> None:
    schedule = AcademicEvidenceProfile(
        evidence_id=_id(7),
        topic=AcademicTopic.CURRENT_AVAILABILITY,
        campus="Westville",
        term="Spring",
        year="2031",
        session="Full Term",
        availability_basis=AvailabilityBasis.CURRENT_SCHEDULE,
    )
    rules = AcademicScopeRules()

    supported = rules.evaluate(
        topic=AcademicTopic.CURRENT_AVAILABILITY,
        context={
            "campus": "Westville",
            "term": "Spring",
            "year": "2031",
            "session": "Full Term",
        },
        profiles=(schedule,),
    )
    mismatched = rules.evaluate(
        topic=AcademicTopic.CURRENT_AVAILABILITY,
        context={
            "campus": "Hammond",
            "term": "Spring",
            "year": "2031",
            "session": "Full Term",
        },
        profiles=(schedule,),
    )

    assert supported.outcome is AcademicScopeOutcome.SUPPORTED
    assert supported.can_confirm_current_availability is True
    assert mismatched.outcome is AcademicScopeOutcome.LIMITED
    assert mismatched.limitation is AcademicLimitation.CONTEXT_UNSUPPORTED
    assert mismatched.can_confirm_current_availability is False

