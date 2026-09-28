from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RetrievalSource:
    title: str
    url: str
    content: str
    campus_scope: str | None = None
    academic_term: str | None = None
    is_conflicting: bool = False


@dataclass(frozen=True, slots=True)
class ValidationCase:
    name: str
    question: str
    expected: str = "supported"
    campus_scope: str | None = None
    academic_term: str | None = None

    def __post_init__(self) -> None:
        if self.expected not in {"supported", "no_match", "conflict"}:
            raise ValueError("expected must be one of: supported, no_match, conflict")


_DEFAULT_CASES: list[ValidationCase] = [
    ValidationCase(
        name="parking_support",
        question="What are the parking rules for the Hammond campus?",
        expected="supported",
        campus_scope="hammond",
    ),
    ValidationCase(
        name="deadline_support",
        question="When is the fall registration deadline?",
        expected="supported",
        academic_term="fall",
    ),
    ValidationCase(
        name="no_match",
        question="What is the policy for final grades in chemistry?",
        expected="no_match",
    ),
    ValidationCase(
        name="conflicting_sources",
        question="When is the registration deadline?",
        expected="conflict",
        academic_term="fall",
    ),
]


def _normalize(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.strip().lower().split())
    return cleaned or None


def _tokenize(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", _normalize(value) or "") if len(token) > 2}


def _scope_matches(candidate: str | None, requested: str | None) -> bool:
    if requested is None:
        return True
    if candidate is None:
        return True
    candidate_norm = _normalize(candidate)
    requested_norm = _normalize(requested)
    if candidate_norm in {"both", "all"}:
        return True
    return candidate_norm == requested_norm


def _match_relevance(
    source: RetrievalSource,
    *,
    question: str,
    campus_scope: str | None,
    academic_term: str | None,
) -> bool:
    content = _normalize(source.content) or ""
    title = _normalize(source.title) or ""
    question_text = _normalize(question) or ""
    if not question_text:
        return False

    if question_text in content or content in question_text:
        return True

    question_tokens = _tokenize(question_text)
    source_tokens = _tokenize(title) | _tokenize(content)
    if question_tokens and question_tokens & source_tokens:
        return True

    if campus_scope and not _scope_matches(source.campus_scope, campus_scope):
        return False
    if academic_term and source.academic_term and _normalize(source.academic_term) != _normalize(academic_term):
        return False

    if campus_scope and source.campus_scope and _scope_matches(source.campus_scope, campus_scope):
        return True
    if academic_term and source.academic_term and _normalize(source.academic_term) == _normalize(academic_term):
        return True

    return False


def _evaluate_case(corpus: list[RetrievalSource], case: ValidationCase) -> dict[str, Any]:
    matched = [
        source
        for source in corpus
        if _match_relevance(
            source,
            question=case.question,
            campus_scope=case.campus_scope,
            academic_term=case.academic_term,
        )
    ]

    citation_coverage = bool(matched)
    scope_ok = all(_scope_matches(source.campus_scope, case.campus_scope) for source in matched)
    if case.academic_term is not None:
        scope_ok = scope_ok and all(
            source.academic_term is None or _normalize(source.academic_term) == _normalize(case.academic_term)
            for source in matched
        )

    conflict_detected = any(source.is_conflicting for source in corpus)

    if case.expected == "no_match":
        passed = not matched
    elif case.expected == "conflict":
        passed = not conflict_detected
    else:
        passed = citation_coverage and scope_ok and not any(source.is_conflicting for source in matched)

    return {
        "name": case.name,
        "question": case.question,
        "expected": case.expected,
        "status": "passed" if passed else "failed",
        "matched_sources": [source.url for source in matched],
        "citation_coverage": citation_coverage,
        "scope_filtering": scope_ok,
        "no_match": case.expected == "no_match" and not matched,
        "conflict_detected": conflict_detected,
    }


def validate_corpus(
    corpus: list[RetrievalSource] | tuple[RetrievalSource, ...] | None,
    *,
    cases: list[ValidationCase] | tuple[ValidationCase, ...] | None = None,
) -> dict[str, Any]:
    """Run representative retrieval checks over a corpus and return a validation report.

    The report includes per-case pass/fail details and a summary of the coverage
    checks most relevant to T020: citation coverage, scope filtering,
    no-match safety, and conflicting-source detection.
    """
    sources = list(corpus or [])
    validation_cases = list(cases or _DEFAULT_CASES)

    results = [_evaluate_case(sources, case) for case in validation_cases]
    passed_cases = sum(1 for result in results if result["status"] == "passed")
    failed_cases = len(results) - passed_cases

    summary = {
        "total_cases": len(results),
        "passed_cases": passed_cases,
        "failed_cases": failed_cases,
        "passed": failed_cases == 0,
        "checks": {
            "citation_coverage": sum(1 for result in results if result["citation_coverage"]),
            "scope_filtering": sum(1 for result in results if result["scope_filtering"]),
            "no_match_behavior": sum(1 for result in results if result["no_match"]),
            "conflicting_sources": sum(1 for result in results if result["conflict_detected"]),
        },
    }

    return {
        "passed": failed_cases == 0,
        "results": results,
        "summary": summary,
    }


def main() -> None:
    """CLI entry point for local corpus validation runs."""
    report = validate_corpus([])
    print(report["summary"])


if __name__ == "__main__":
    main()
