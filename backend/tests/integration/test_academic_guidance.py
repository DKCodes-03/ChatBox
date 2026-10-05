"""Integrated US4 coverage for source-backed academic guidance boundaries."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from app.academic.prerequisites import PrerequisiteOperator, parse_course_relations
from app.academic.programs import (
    AcademicEvidenceProfile,
    AcademicLimitation,
    AcademicScopeOutcome,
    AcademicScopeRules,
    AcademicTopic,
    AvailabilityBasis,
    ProgramStatus,
)
from app.api.processor import RuntimeEvidenceRetriever
from app.config import Settings
from app.context import StudentContext
from app.generation.schemas import ReasonCode
from app.models.enums import CourseRelationType
from app.models.guidance import CourseRelation
from bs4 import BeautifulSoup

pytestmark = pytest.mark.integration

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "corpus" / "us4"


def _load_json(name: str) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8")),
    )


EXPECTED = _load_json("expected-results.json")
METADATA = _load_json("fixture-metadata.json")
CASES = cast(dict[str, dict[str, Any]], {item["id"]: item for item in EXPECTED["cases"]})
SOURCES = cast(tuple[dict[str, Any], ...], tuple(METADATA["sources"]))
DATABASE_INTEGRATION_ENABLED = os.environ.get("DATABASE_INTEGRATION_TESTS") == "true"
requires_database = pytest.mark.skipif(
    not DATABASE_INTEGRATION_ENABLED,
    reason="run through the Compose test-academic-us4 service",
)


@pytest.fixture(scope="module")
def database_retriever() -> Iterator[RuntimeEvidenceRetriever]:
    retriever = RuntimeEvidenceRetriever.from_settings(Settings())
    try:
        yield retriever
    finally:
        retriever.close()


def _source_by_evidence_id(evidence_id: str) -> dict[str, Any]:
    matches = [
        source
        for source in SOURCES
        if any(item["evidence_id"] == evidence_id for item in source["evidence"])
    ]
    assert len(matches) == 1
    return matches[0]


def _evidence(source: dict[str, Any]) -> dict[str, Any]:
    items = cast(list[dict[str, Any]], source["evidence"])
    assert len(items) == 1
    item = items[0]
    soup = BeautifulSoup(
        (FIXTURE_DIR / source["fixture_file"]).read_text(encoding="utf-8"),
        "html.parser",
    )
    section = soup.find(id=item["anchor"])
    assert section is not None
    section_text = " ".join(section.get_text(" ", strip=True).split())
    assert all(marker in section_text for marker in item["text_contains"])
    return item


def _academic_profile(source: dict[str, Any]) -> AcademicEvidenceProfile:
    profile = cast(dict[str, Any] | None, source["academic_profile"])
    assert profile is not None
    evidence = _evidence(source)
    applicability = cast(dict[str, Any], source["applicability"])
    scope = cast(dict[str, Any], evidence.get("scope", {}))
    return AcademicEvidenceProfile(
        evidence_id=UUID(evidence["evidence_id"]),
        topic=AcademicTopic(profile["topic"]),
        campus=applicability.get("campus"),
        term=applicability.get("term"),
        year=scope.get("year"),
        session=applicability.get("session"),
        program=applicability.get("program"),
        student_level=applicability.get("student_level"),
        catalog_year=applicability.get("catalog_year"),
        program_status=(
            ProgramStatus(profile["program_status"])
            if profile.get("program_status") is not None
            else None
        ),
        availability_basis=(
            AvailabilityBasis(profile["availability_basis"])
            if profile.get("availability_basis") is not None
            else None
        ),
    )


def _qualified_profiles(topic: AcademicTopic) -> tuple[AcademicEvidenceProfile, ...]:
    profiles: list[AcademicEvidenceProfile] = []
    for source in SOURCES:
        profile = source["academic_profile"]
        if (
            source["source_status"] == "eligible"
            and source["extraction_status"] == "complete"
            and source["qualification"]["status"] == "passed"
            and profile is not None
            and profile["topic"] == topic.value
        ):
            profiles.append(_academic_profile(source))
    return tuple(profiles)


def test_explicit_program_existence_is_limited_to_its_catalog_scope() -> None:
    case = CASES["explicit-program-exists"]
    source = _source_by_evidence_id(case["expected_evidence_ids"][0])
    content = cast(dict[str, Any], _evidence(source)["structured_content"])
    rules = AcademicScopeRules()

    decision = rules.evaluate(
        topic=AcademicTopic.PROGRAM,
        context=case["context"],
        profiles=_qualified_profiles(AcademicTopic.PROGRAM),
    )

    assert content["program_status"] == case["expected_program_status"]
    assert decision.outcome is AcademicScopeOutcome.SUPPORTED
    assert decision.evidence_ids == tuple(UUID(item) for item in case["expected_evidence_ids"])
    assert decision.program_status is ProgramStatus(case["expected_program_status"])
    assert decision.can_claim_program_absence is False
    assert content["personal_admission_status_established"] is False
    assert content["current_availability_established"] is False

    wrong_catalog = {**case["context"], "catalog_year": "2031-2032"}
    mismatched = rules.evaluate(
        topic=AcademicTopic.PROGRAM,
        context=wrong_catalog,
        profiles=_qualified_profiles(AcademicTopic.PROGRAM),
    )
    assert mismatched.outcome is AcademicScopeOutcome.LIMITED
    assert mismatched.limitation is AcademicLimitation.CONTEXT_UNSUPPORTED
    assert mismatched.program_status is None
    assert mismatched.can_claim_program_absence is False


def test_incomplete_catalog_inventory_cannot_support_program_absence() -> None:
    case = CASES["incomplete-inventory-no-negative-inference"]
    source = _source_by_evidence_id(case["ineligible_evidence_ids"][0])
    content = cast(dict[str, Any], _evidence(source)["structured_content"])
    matching_qualified_profiles = tuple(
        profile
        for profile in _qualified_profiles(AcademicTopic.PROGRAM)
        if profile.program == case["context"]["program"]
        and profile.catalog_year == case["context"]["catalog_year"]
    )

    decision = AcademicScopeRules().evaluate(
        topic=AcademicTopic.PROGRAM,
        context=case["context"],
        profiles=matching_qualified_profiles,
    )

    assert source["source_status"] == "quarantined"
    assert source["extraction_status"] == "incomplete"
    assert source["qualification"]["status"] == "failed"
    assert content["complete"] is False
    assert content["may_support_negative_program_claim"] is False
    assert decision.outcome is AcademicScopeOutcome.LIMITED
    assert decision.limitation is AcademicLimitation(case["expected_limitation"])
    assert decision.evidence_ids == ()
    assert decision.program_status is None
    assert decision.can_claim_program_absence is False


def test_prerequisite_summary_preserves_groups_grades_corequisite_and_scope() -> None:
    case = CASES["grouped-prerequisites"]
    source = _source_by_evidence_id(case["expected_evidence_ids"][0])
    _evidence(source)
    relations = tuple(
        CourseRelation(
            id=UUID(item["relation_id"]),
            evidence_block_id=UUID(item["evidence_block_id"]),
            course_code=item["course_code"],
            related_course_code=item["related_course_code"],
            relation=CourseRelationType(item["relation"]),
            group_expression=item["group_expression"],
            campus=item["campus"],
            catalog_year=item["catalog_year"],
        )
        for item in source["course_relations"]
    )

    summaries = parse_course_relations(relations)

    assert len(summaries) == 1
    summary = summaries[0]
    expected = case["expected_prerequisite_expression"]
    assert summary.evidence_block_id == UUID(case["expected_evidence_ids"][0])
    assert summary.course_code == "SYN 35000"
    assert summary.campus == case["context"]["campus"]
    assert summary.catalog_year == case["context"]["catalog_year"]
    assert summary.operator is PrerequisiteOperator(expected["operator"])
    assert [group.operator.value for group in summary.prerequisite_groups] == [
        group["operator"] for group in expected["groups"]
    ]
    assert [
        [
            {"course_code": item.course_code, "minimum_grade": item.minimum_grade}
            for item in group.requirements
        ]
        for group in summary.prerequisite_groups
    ] == [group["requirements"] for group in expected["groups"]]
    assert [
        {
            "course_code": item.course_code,
            "concurrent_enrollment_allowed": item.concurrent_enrollment_allowed,
        }
        for item in summary.corequisites
    ] == case["expected_corequisites"]
    assert case["must_not_claim_personal_eligibility"] is True


@pytest.mark.parametrize(
    ("case_id", "topic"),
    [
        ("graduate-admission-general-steps", AcademicTopic.GRADUATE_ADMISSION),
        ("plan-of-study-steps", AcademicTopic.PLAN_OF_STUDY),
        ("graduation-application-steps", AcademicTopic.GRADUATION),
    ],
)
def test_academic_procedures_allow_general_steps_but_not_personal_decisions(
    case_id: str,
    topic: AcademicTopic,
) -> None:
    case = CASES[case_id]
    source = _source_by_evidence_id(case["expected_evidence_ids"][0])
    content = cast(dict[str, Any], _evidence(source)["structured_content"])
    profiles = _qualified_profiles(topic)
    rules = AcademicScopeRules()

    general = rules.evaluate(topic=topic, context=case["context"], profiles=profiles)
    personal = rules.evaluate(
        topic=topic,
        context=case["context"],
        profiles=profiles,
        requests_personal_decision=True,
    )

    assert len(content["steps"]) == case["expected_step_count"]
    if "expected_required_approval" in case:
        assert content["required_approval"] == case["expected_required_approval"]
    assert general.outcome is AcademicScopeOutcome.SUPPORTED
    assert general.evidence_ids == tuple(UUID(item) for item in case["expected_evidence_ids"])
    assert general.selected_context == StudentContext.model_validate(case["context"])
    assert general.allows_general_guidance is True
    assert general.allows_personal_decision is False
    assert personal.outcome is AcademicScopeOutcome.LIMITED
    assert personal.limitation is AcademicLimitation.PERSONAL_DECISION
    assert personal.allows_general_guidance is True
    assert personal.allows_personal_decision is False


def test_typical_offering_does_not_infer_current_availability() -> None:
    case = CASES["typical-offering-not-current-availability"]
    source = _source_by_evidence_id(case["expected_evidence_ids"][0])
    content = cast(dict[str, Any], _evidence(source)["structured_content"])
    referrals = cast(list[dict[str, Any]], source["office_referrals"])

    decision = AcademicScopeRules().evaluate(
        topic=AcademicTopic.CURRENT_AVAILABILITY,
        context=case["context"],
        profiles=_qualified_profiles(AcademicTopic.CURRENT_AVAILABILITY),
    )

    assert content["availability_basis"] == case["expected_availability_basis"]
    assert content["current_availability_established"] is False
    assert decision.outcome is AcademicScopeOutcome.LIMITED
    assert decision.limitation is AcademicLimitation(case["expected_limitation"])
    assert decision.evidence_ids == tuple(UUID(item) for item in case["expected_evidence_ids"])
    assert decision.can_confirm_current_availability is False
    assert len(referrals) == 1
    assert referrals[0]["referral_id"] == case["expected_referral_id"]
    assert referrals[0]["expected_eligible"] is True


@requires_database
@pytest.mark.parametrize(
    ("case_id", "expected_topic"),
    [
        ("explicit-program-exists", AcademicTopic.PROGRAM),
        ("graduate-admission-general-steps", AcademicTopic.GRADUATE_ADMISSION),
        ("plan-of-study-steps", AcademicTopic.PLAN_OF_STUDY),
        ("graduation-application-steps", AcademicTopic.GRADUATION),
    ],
)
def test_production_retriever_applies_academic_scope_rules(
    database_retriever: RuntimeEvidenceRetriever,
    case_id: str,
    expected_topic: AcademicTopic,
) -> None:
    case = CASES[case_id]

    result = database_retriever.retrieve(query=case["question"], context=case["context"])

    assert result.failure_reason is None
    assert [str(item.evidence_id) for item in result.evidence] == case["expected_evidence_ids"]
    assert result.academic_scope is not None
    assert result.academic_scope.topic is expected_topic
    assert result.academic_scope.outcome is AcademicScopeOutcome.SUPPORTED
    assert result.academic_scope.allows_general_guidance is True
    assert result.academic_scope.allows_personal_decision is False


@requires_database
def test_production_retriever_requires_structured_prerequisite_relations(
    database_retriever: RuntimeEvidenceRetriever,
) -> None:
    case = CASES["grouped-prerequisites"]

    result = database_retriever.retrieve(query=case["question"], context=case["context"])

    assert result.failure_reason is None
    assert [str(item.evidence_id) for item in result.evidence] == case["expected_evidence_ids"]
    assert len(result.prerequisites) == 1
    summary = result.prerequisites[0]
    assert summary.operator is PrerequisiteOperator.OR
    assert [group.operator for group in summary.prerequisite_groups] == [
        PrerequisiteOperator.AND,
        PrerequisiteOperator.SINGLE,
    ]
    assert summary.corequisites[0].course_code == "SYN 35001"
    assert summary.corequisites[0].concurrent_enrollment_allowed is True


@requires_database
def test_production_retriever_blocks_typical_offering_as_current_availability(
    database_retriever: RuntimeEvidenceRetriever,
) -> None:
    case = CASES["typical-offering-not-current-availability"]

    result = database_retriever.retrieve(query=case["question"], context=case["context"])

    assert result.failure_reason is None
    assert result.academic_scope is not None
    assert result.academic_scope.outcome is AcademicScopeOutcome.LIMITED
    assert result.academic_scope.limitation is AcademicLimitation.CURRENT_AVAILABILITY_UNCONFIRMED
    assert result.academic_scope.can_confirm_current_availability is False
    assert len(result.referrals) == 1


@requires_database
def test_production_retriever_does_not_borrow_context_for_incomplete_inventory(
    database_retriever: RuntimeEvidenceRetriever,
) -> None:
    result = database_retriever.retrieve(query="INCOMPLETE-CATALOG-SENTINEL", context={})

    assert result.evidence == ()
    assert result.clarification is None
    assert result.failure_reason in {
        ReasonCode.MISSING_EVIDENCE,
        ReasonCode.SOURCE_UNAVAILABLE,
    }
    assert result.academic_scope is None
