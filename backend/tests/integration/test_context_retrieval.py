"""Integrated US3 coverage for context gating, tables, dates, and relationships."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from app.api.processor import RuntimeEvidenceRetriever
from app.config import Settings
from app.context import ContextField, StudentContext
from app.generation.schemas import AnswerOutcome, AnswerSegment, SegmentKind, StructuredAnswer
from app.generation.validator import AnswerValidator, ValidationFailureCode
from app.retrieval.context_gate import ContextGate
from app.retrieval.search import (
    EvidenceApplicability,
    RetrievalChannel,
    RetrievalScope,
    RetrievedEvidence,
)
from app.sessions import SessionManager
from bs4 import BeautifulSoup
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.integration

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "corpus" / "us3"


def _load_json(name: str) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8")),
    )


EXPECTED = _load_json("expected-results.json")
METADATA = _load_json("fixture-metadata.json")
CASES = cast(dict[str, dict[str, Any]], {item["id"]: item for item in EXPECTED["cases"]})
SOURCES = cast(
    dict[str, dict[str, Any]],
    {item["source_id"]: item for item in METADATA["sources"]},
)
SCHEDULE_SOURCES = tuple(
    source
    for source in SOURCES.values()
    if source["applicability"]["topic"] == "synthetic_academic_schedule"
)
DATABASE_INTEGRATION_ENABLED = os.environ.get("DATABASE_INTEGRATION_TESTS") == "true"
requires_database = pytest.mark.skipif(
    not DATABASE_INTEGRATION_ENABLED,
    reason="run through the Compose test-context-us3 service",
)


def _source_by_evidence_id(evidence_id: str) -> dict[str, Any]:
    matches = [
        source
        for source in SOURCES.values()
        if any(item["evidence_id"] == evidence_id for item in source["evidence"])
    ]
    assert len(matches) == 1
    return matches[0]


def _student_context(source: dict[str, Any]) -> StudentContext:
    evidence = source["evidence"][0]
    values = {
        key: value
        for key, value in source["applicability"].items()
        if key
        in {
            "campus",
            "term",
            "session",
            "program",
            "student_level",
            "catalog_year",
        }
        and value is not None
    }
    year = evidence.get("scope", {}).get("year")
    if year is not None:
        values["year"] = year
    return StudentContext.model_validate(values)


def _schedule_profiles() -> tuple[StudentContext, ...]:
    return tuple(_student_context(source) for source in SCHEDULE_SOURCES)


def _retrieved_evidence(source: dict[str, Any]) -> RetrievedEvidence:
    item = source["evidence"][0]
    soup = BeautifulSoup(
        (FIXTURE_DIR / source["fixture_file"]).read_text(encoding="utf-8"),
        "html.parser",
    )
    section = soup.find(id=item["anchor"])
    assert section is not None
    text = " ".join(section.get_text(" ", strip=True).split())
    assert all(marker in text for marker in item["text_contains"])
    applicability = dict(source["applicability"])
    applicability["year"] = item.get("scope", {}).get("year")
    return RetrievedEvidence(
        evidence_id=UUID(item["evidence_id"]),
        version_id=UUID(source["version_id"]),
        source_id=UUID(source["source_id"]),
        source_title=source["title"],
        canonical_url=source["url"],
        ordinal=item["ordinal"],
        heading_path=tuple(item["heading_path"]),
        page=None,
        anchor=item["anchor"],
        text=text,
        structured_content=item["structured_content"],
        topic_key=item["topic_key"],
        scope=item.get("scope", {}),
        applicability=EvidenceApplicability(**applicability),
        model_revision="fixture-minilm-v1",
        channel=RetrievalChannel.FULL_TEXT,
        rank=1,
        ranking_value=1.0,
    )


def _scope(source: dict[str, Any]) -> RetrievalScope:
    context = _student_context(source)
    applicability = source["applicability"]
    return RetrievalScope(
        topic=applicability["topic"],
        institution=applicability["institution"],
        **context.as_mapping(),
    )


def _candidate(text: str, evidence_id: UUID) -> StructuredAnswer:
    return StructuredAnswer(
        outcome=AnswerOutcome.ANSWER,
        segments=(
            AnswerSegment(
                kind=SegmentKind.EXPLANATION,
                text=text,
                evidence_ids=(evidence_id,),
            ),
        ),
    )


def _structured_content(evidence: RetrievedEvidence) -> dict[str, Any]:
    return cast(dict[str, Any], evidence.structured_content)


@pytest.fixture(scope="module")
def database_retriever() -> Iterator[RuntimeEvidenceRetriever]:
    settings = Settings()
    retriever = RuntimeEvidenceRetriever.from_settings(settings)
    try:
        yield retriever
    finally:
        retriever.close()


def test_missing_context_requests_campus_before_retrieval_or_generation() -> None:
    case = CASES["missing-campus"]

    decision = ContextGate().evaluate(context=case["context"], profiles=_schedule_profiles())

    assert decision.requires_clarification
    assert decision.selected_context == StudentContext()
    assert decision.clarification is not None
    assert decision.clarification.outcome is AnswerOutcome.CLARIFICATION
    assert decision.clarification.reason_code == case["expected_reason_code"]
    assert decision.clarification.clarification is not None
    assert decision.clarification.clarification.fields == (ContextField(case["expected_field"]),)
    assert decision.clarification.clarification.options == tuple(case["expected_options"])
    assert case["must_not_call_generation"] is True


def test_term_mismatch_is_removed_and_valid_terms_are_requested() -> None:
    case = CASES["term-mismatch"]

    decision = ContextGate().evaluate(context=case["context"], profiles=_schedule_profiles())

    assert decision.requires_clarification
    assert decision.selected_context == StudentContext(campus="Hammond")
    assert decision.selected_context.term is None
    assert decision.clarification is not None
    assert decision.clarification.clarification is not None
    assert decision.clarification.clarification.fields == (ContextField.TERM,)
    assert decision.clarification.clarification.options == tuple(case["expected_options"])
    assert case["must_not_select_term"] not in decision.selected_context.as_mapping().values()


def test_refund_rows_preserve_dates_percentages_and_past_status() -> None:
    case = CASES["hammond-full-term-refund-table"]
    source = _source_by_evidence_id(case["expected_evidence_id"])
    evidence = _retrieved_evidence(source)
    structured_rows = cast(list[dict[str, Any]], _structured_content(evidence)["rows"])

    for expected_row in case["expected_rows"]:
        matches = [row for row in structured_rows if row["event"] == expected_row["event"]]
        assert len(matches) == 1
        row = matches[0]
        assert row["deadline"] == expected_row["deadline"]
        assert row["refund_percentage"] == expected_row["refund_percentage"]
        assert row["time_zone"] == expected_row["time_zone"]

    observed_at = datetime.fromisoformat(EXPECTED["observed_at"].replace("Z", "+00:00"))
    correct = _candidate(
        (
            "The 80 percent refund deadline was September 13, 2030 at 5:00 p.m. "
            "Central Time."
        ),
        evidence.evidence_id,
    )
    correct_result = AnswerValidator().validate(
        correct,
        evidence=(evidence,),
        scope=_scope(source),
        observed_at=observed_at,
    )
    assert correct_result.accepted is True

    unlabeled = _candidate(
        "The 80 percent refund deadline is September 13, 2030 at 5:00 p.m. Central Time.",
        evidence.evidence_id,
    )
    unlabeled_result = AnswerValidator().validate(
        unlabeled,
        evidence=(evidence,),
        scope=_scope(source),
        observed_at=observed_at,
    )
    assert ValidationFailureCode.PAST_DATE_UNLABELED in unlabeled_result.failures

    mismatched = _candidate(
        (
            "The 80 percent refund deadline was September 27, 2030 at 5:00 p.m. "
            "Central Time."
        ),
        evidence.evidence_id,
    )
    mismatch_result = AnswerValidator().validate(
        mismatched,
        evidence=(evidence,),
        scope=_scope(source),
        observed_at=observed_at,
    )
    assert ValidationFailureCode.TABLE_RELATIONSHIP_MISMATCH in mismatch_result.failures

    future_mislabeled = _candidate(
        "The 60 percent refund deadline was September 27, 2030 at 5:00 p.m. Central Time.",
        evidence.evidence_id,
    )
    future_result = AnswerValidator().validate(
        future_mislabeled,
        evidence=(evidence,),
        scope=_scope(source),
        observed_at=observed_at,
    )
    assert ValidationFailureCode.UNSUPPORTED_CLAIM in future_result.failures


def test_session_context_selects_only_the_first_eight_weeks_profile() -> None:
    case = CASES["session-selection"]

    decision = ContextGate().evaluate(context=case["context"], profiles=_schedule_profiles())

    assert not decision.requires_clarification
    matching_profiles = [
        profile
        for profile in EXPECTED["schedule_context_profiles"]
        if StudentContext.model_validate(
            {key: value for key, value in profile.items() if key != "evidence_id"}
        )
        == decision.selected_context
    ]
    assert len(matching_profiles) == 1
    assert matching_profiles[0]["evidence_id"] == case["expected_evidence_id"]
    source = _source_by_evidence_id(case["expected_evidence_id"])
    table_text = json.dumps(source["evidence"][0]["structured_content"], sort_keys=True)
    assert case["expected_80_percent_deadline"] in table_text
    assert case["prohibited_full_term_80_percent_deadline"] not in table_text


@pytest.mark.parametrize("case_id", ["campus-correction", "term-correction"])
def test_explicit_correction_replaces_old_context_and_rechecks_evidence(case_id: str) -> None:
    case = CASES[case_id]
    observed_at = datetime(2030, 9, 20, 12, tzinfo=UTC)
    manager = SessionManager(clock=lambda: observed_at, auto_start=False)
    try:
        created = manager.create_session()
        first = manager.admit_student_message(
            created.token,
            EXPECTED["schedule_question"],
            context=case["old_context"],
        )
        _, first_context = first.buffer.snapshot()
        assert first_context == case["old_context"]
        manager.complete_generation(first, assistant_message=None)

        corrected = manager.admit_student_message(
            created.token,
            "Use my corrected synthetic context.",
            context=case["corrected_context"],
        )
        _, corrected_context = corrected.buffer.snapshot()
        assert corrected_context == case["corrected_context"]
        assert corrected_context != first_context

        decision = ContextGate().evaluate(
            context=corrected_context,
            profiles=_schedule_profiles(),
        )
        assert not decision.requires_clarification
        selected_profile = next(
            profile
            for profile in EXPECTED["schedule_context_profiles"]
            if StudentContext.model_validate(
                {key: value for key, value in profile.items() if key != "evidence_id"}
            )
            == decision.selected_context
        )
        assert selected_profile["evidence_id"] == case["corrected_evidence_id"]
        assert selected_profile["evidence_id"] != case["old_evidence_id"]

        corrected_source = _source_by_evidence_id(case["corrected_evidence_id"])
        corrected_table = json.dumps(
            corrected_source["evidence"][0]["structured_content"],
            sort_keys=True,
        )
        assert case["expected_corrected_80_percent_deadline"] in corrected_table
        assert case["prohibited_old_80_percent_deadline"] not in corrected_table
        manager.complete_generation(corrected, assistant_message=None)
    finally:
        manager.shutdown()


def test_prerequisite_groups_and_corequisite_remain_distinct() -> None:
    case = CASES["prerequisite-relationships"]
    source = _source_by_evidence_id(case["expected_evidence_id"])
    evidence = _retrieved_evidence(source)
    content = _structured_content(evidence)
    expression = cast(dict[str, Any], content["prerequisite_expression"])

    expected_expression = case["expected_prerequisite_expression"]
    assert expression["operator"] == expected_expression["operator"]
    actual_groups = cast(list[dict[str, Any]], expression["groups"])
    expected_groups = cast(list[dict[str, Any]], expected_expression["groups"])
    assert len(actual_groups) == len(expected_groups) == 2
    for actual, expected in zip(actual_groups, expected_groups, strict=True):
        actual_requirements = cast(list[dict[str, Any]], actual["requirements"])
        assert actual["operator"] == expected["operator"]
        assert [item["course_code"] for item in actual_requirements] == expected["courses"]
        assert {item["minimum_grade"] for item in actual_requirements} == {
            expected["minimum_grade"]
        }

    corequisites = cast(list[dict[str, Any]], content["corequisites"])
    assert [item["course_code"] for item in corequisites] == case["expected_corequisites"]
    assert all(item["concurrent_enrollment_allowed"] for item in corequisites)
    assert case["must_not_claim_personal_eligibility"] is True
    assert case["must_not_claim_current_availability"] is True


@requires_database
def test_postgres_runtime_retrieval_clarifies_and_requeries_corrected_context(
    database_retriever: RuntimeEvidenceRetriever,
) -> None:
    question = EXPECTED["schedule_question"]
    missing_case = CASES["missing-campus"]
    missing = database_retriever.retrieve(query=question, context={})
    assert missing.evidence == ()
    assert missing.clarification is not None
    assert missing.clarification.outcome is AnswerOutcome.CLARIFICATION
    assert missing.clarification.clarification is not None
    assert missing.clarification.clarification.fields == (ContextField.CAMPUS,)
    assert missing.clarification.clarification.options == tuple(missing_case["expected_options"])

    mismatch_case = CASES["term-mismatch"]
    mismatch = database_retriever.retrieve(query=question, context=mismatch_case["context"])
    assert mismatch.evidence == ()
    assert mismatch.clarification is not None
    assert mismatch.clarification.clarification is not None
    assert mismatch.clarification.clarification.fields == (ContextField.TERM,)
    assert mismatch.clarification.clarification.options == tuple(
        mismatch_case["expected_options"]
    )

    correction_case = CASES["campus-correction"]
    original = database_retriever.retrieve(
        query=question,
        context=correction_case["old_context"],
    )
    corrected = database_retriever.retrieve(
        query=question,
        context=correction_case["corrected_context"],
    )
    assert [str(item.evidence_id) for item in original.evidence] == [
        correction_case["old_evidence_id"]
    ]
    assert [str(item.evidence_id) for item in corrected.evidence] == [
        correction_case["corrected_evidence_id"]
    ]
    assert original.selected_context == StudentContext.model_validate(
        correction_case["old_context"]
    )
    assert corrected.selected_context == StudentContext.model_validate(
        correction_case["corrected_context"]
    )
    corrected_rows = cast(
        list[dict[str, Any]],
        _structured_content(corrected.evidence[0])["rows"],
    )
    corrected_table = json.dumps(corrected_rows, sort_keys=True)
    assert correction_case["expected_corrected_80_percent_deadline"] in corrected_table
    assert correction_case["prohibited_old_80_percent_deadline"] not in corrected_table

    session_case = CASES["session-selection"]
    session_result = database_retriever.retrieve(
        query=question,
        context=session_case["context"],
    )
    assert [str(item.evidence_id) for item in session_result.evidence] == [
        session_case["expected_evidence_id"]
    ]


@requires_database
def test_postgres_runtime_retrieves_prerequisite_evidence_and_relations(
    database_retriever: RuntimeEvidenceRetriever,
) -> None:
    case = CASES["prerequisite-relationships"]
    result = database_retriever.retrieve(
        query=case["question"],
        context=case["context"],
    )
    assert [str(item.evidence_id) for item in result.evidence] == [
        case["expected_evidence_id"]
    ]
    assert result.selected_context == StudentContext.model_validate(case["context"])

    settings = Settings()
    engine = create_engine(settings.database_url(), hide_parameters=True)
    try:
        with engine.connect() as connection:
            relations = connection.execute(
                text(
                    """
                    SELECT related_course_code, relation::text, group_expression
                    FROM course_relations
                    WHERE evidence_block_id = :evidence_block_id
                    ORDER BY related_course_code
                    """
                ),
                {"evidence_block_id": UUID(case["expected_evidence_id"])},
            ).mappings().all()
    finally:
        engine.dispose()

    assert [(row["related_course_code"], row["relation"]) for row in relations] == [
        ("SYN 11000", "prerequisite"),
        ("SYN 12000", "prerequisite"),
        ("SYN 13000", "prerequisite"),
        ("SYN 25001", "corequisite"),
    ]
    prerequisite_groups = {
        row["related_course_code"]: row["group_expression"]
        for row in relations
        if row["relation"] == "prerequisite"
    }
    assert prerequisite_groups["SYN 11000"]["group"] == 1
    assert prerequisite_groups["SYN 11000"]["group_operator"] == "and"
    assert prerequisite_groups["SYN 12000"]["group"] == 1
    assert prerequisite_groups["SYN 13000"]["group"] == 2
