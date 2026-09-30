from app.services.escalation_service import (
    EscalationService,
    build_safe_limitation_message,
    lookup_escalation_destination,
)


def test_looks_up_financial_aid_destination_for_personalized_questions() -> None:
    destination = lookup_escalation_destination(
        "Can you tell me whether my financial aid award is still active?",
        issue="personalized_record",
    )

    assert destination is not None
    assert destination.category == "financial aid"
    assert "financial aid" in destination.name.lower()
    assert destination.url


def test_builds_safe_limitation_message_with_official_contact() -> None:
    destination = EscalationService.lookup_destination(
        "My registration eligibility is missing from the portal.",
        issue="personalized_record",
    )
    message = build_safe_limitation_message(
        "I can’t provide a reliable answer because the evidence is incomplete.",
        destination=destination,
    )

    assert "cannot provide a reliable answer" in message.lower()
    assert "contact" in message.lower()
    assert destination is not None
    assert destination.name in message
