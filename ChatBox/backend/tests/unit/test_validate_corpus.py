from __future__ import annotations

from ingestion.validate_corpus import RetrievalSource, ValidationCase, validate_corpus


def test_validate_corpus_detects_scope_and_conflict_issues() -> None:
    corpus = [
        RetrievalSource(
            title="Parking regulations",
            url="https://pnw.edu/parking/rules",
            content="Parking permits are required on the Hammond campus.",
            campus_scope="hammond",
            academic_term="fall",
        ),
        RetrievalSource(
            title="Parking regulations",
            url="https://pnw.edu/parking/rules-westville",
            content="Parking permits are required on the Westville campus.",
            campus_scope="westville",
            academic_term="fall",
        ),
        RetrievalSource(
            title="Registration deadline",
            url="https://pnw.edu/registrar/deadlines",
            content="The final drop deadline is July 15 for fall registration.",
            campus_scope="both",
            academic_term="fall",
        ),
        RetrievalSource(
            title="Conflicting policy",
            url="https://pnw.edu/policies/conflict",
            content="The registration deadline is August 1 for all students.",
            campus_scope="both",
            academic_term="fall",
            is_conflicting=True,
        ),
    ]

    cases = [
        ValidationCase(
            name="hammond_scope",
            question="What parking rules apply on the Hammond campus?",
            expected="supported",
            campus_scope="hammond",
        ),
        ValidationCase(
            name="wrong_scope",
            question="What parking rules apply on the Hammond campus?",
            expected="supported",
            campus_scope="westville",
        ),
        ValidationCase(
            name="no_match",
            question="What is the policy for final grades in chemistry?",
            expected="no_match",
        ),
        ValidationCase(
            name="conflict",
            question="When is the fall registration deadline?",
            expected="conflict",
        ),
    ]

    report = validate_corpus(corpus, cases=cases)

    assert report["passed"] is False
    assert {result["name"] for result in report["results"]} == {
        "hammond_scope",
        "wrong_scope",
        "no_match",
        "conflict",
    }
    assert report["summary"]["failed_cases"] >= 2
    assert report["summary"]["checks"]["scope_filtering"] >= 1
