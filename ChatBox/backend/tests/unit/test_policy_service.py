from app.services.policy_service import PolicyService


def test_extracts_scope_and_intent_from_question() -> None:
    service = PolicyService()

    analysis = service.extract_intent_and_scope(
        "What are the parking rules on the Hammond campus for fall 2026?"
    )

    assert analysis.intent == "general"
    assert analysis.campus == "hammond"
    assert analysis.academic_term == "fall 2026"


def test_detects_course_program_level_and_personal_record_requests() -> None:
    service = PolicyService()

    course_analysis = service.extract_intent_and_scope(
        "What are the prerequisites for MATH 123?"
    )
    assert course_analysis.course == "math 123"

    program_analysis = service.extract_intent_and_scope(
        "What degree programs are available in computer science?"
    )
    assert program_analysis.program == "computer science"

    level_analysis = service.extract_intent_and_scope(
        "When can an undergraduate student register for spring classes?"
    )
    assert level_analysis.student_level == "undergraduate"

    personal_record = service.extract_intent_and_scope(
        "Can you check whether my financial aid award is still active?"
    )
    assert personal_record.requires_personal_record is True


def test_evidence_gate_accepts_fresh_sources() -> None:
    service = PolicyService()
    assessment = service.assess_evidence(
        [
            {"status": "active", "last_updated": "2026-08-01", "content": "Students must attend orientation."},
            {"status": "active", "last_updated": "2026-09-15", "content": "Orientation is required for new students."},
        ]
    )

    assert assessment.can_answer is True
    assert assessment.has_sufficient_evidence is True
    assert assessment.has_stale_source is False
    assert assessment.has_conflicting_sources is False


def test_evidence_gate_rejects_stale_or_conflicting_sources() -> None:
    service = PolicyService()

    stale_assessment = service.assess_evidence(
        [{"status": "archived", "last_updated": "2018-01-01", "content": "Old policy"}]
    )
    assert stale_assessment.can_answer is False
    assert stale_assessment.has_stale_source is True

    conflict_assessment = service.assess_evidence(
        [
            {"status": "active", "last_updated": "2026-09-01", "content": "Registration closes on Friday."},
            {
                "status": "active",
                "last_updated": "2026-09-02",
                "content": "Registration closes on Monday.",
                "conflict": True,
            },
        ]
    )
    assert conflict_assessment.can_answer is False
    assert conflict_assessment.has_conflicting_sources is True
