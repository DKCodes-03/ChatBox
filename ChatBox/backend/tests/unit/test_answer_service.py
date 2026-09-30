from app.services.answer_service import AnswerService, DraftSupportError


def test_answer_service_builds_evidence_only_prompt() -> None:
    service = AnswerService(model_name="llama3.2:3b")
    evidence = [{
        "title": "Registration Calendar",
        "content": "Spring registration opens on August 1 for undergraduate students.",
    }]

    prompt = service.build_prompt("When does spring registration open?", evidence)

    assert "Use only the provided evidence" in prompt
    assert "Do not use outside knowledge" in prompt
    assert "Spring registration opens on August 1" in prompt


def test_answer_service_rejects_draft_that_exceeds_the_evidence() -> None:
    service = AnswerService(model_name="llama3.2:3b")
    evidence = [{
        "title": "Registration Calendar",
        "content": "Spring registration opens on August 1 for undergraduate students.",
    }]

    try:
        service.validate_support(
            "When does spring registration open?",
            "Spring registration opens on August 1. The deadline is March 1.",
            evidence,
        )
    except DraftSupportError as exc:
        assert "unsupported" in str(exc).lower()
        return

    raise AssertionError("expected unsupported draft to fail validation")
