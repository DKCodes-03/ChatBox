"""Integration coverage for validating the synthetic multi-document US1 corpus."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from app.generation.schemas import (
    AnswerOutcome,
    AnswerSegment,
    ReasonCode,
    SegmentKind,
    StructuredAnswer,
)
from app.generation.validator import AnswerValidator, ValidationFailureCode
from app.retrieval.search import (
    EvidenceApplicability,
    RetrievalChannel,
    RetrievalScope,
    RetrievedEvidence,
)
from bs4 import BeautifulSoup
from pypdf import PdfReader

pytestmark = pytest.mark.integration

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "corpus" / "us1"


def _load_json(name: str) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8")),
    )


def _html_evidence_text(path: Path, anchor: str) -> str:
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    section = soup.find(id=anchor)
    assert section is not None
    return " ".join(section.get_text(" ", strip=True).split())


def _pdf_evidence_text(path: Path, page: int) -> str:
    text = PdfReader(path).pages[page - 1].extract_text()
    assert text is not None
    return " ".join(text.split())


def _retrieved_evidence() -> tuple[RetrievedEvidence, ...]:
    metadata = _load_json("fixture-metadata.json")
    applicability = EvidenceApplicability(**metadata["applicability"])
    evidence_items: list[RetrievedEvidence] = []

    for source in metadata["sources"]:
        fixture_path = FIXTURE_DIR / source["fixture_file"]
        for rank, evidence in enumerate(source["evidence"], start=1):
            if source["media_type"] == "html":
                text = _html_evidence_text(fixture_path, evidence["anchor"])
            else:
                text = _pdf_evidence_text(fixture_path, evidence["page"])
            assert all(marker in text for marker in evidence["text_contains"])
            evidence_items.append(
                RetrievedEvidence(
                    evidence_id=UUID(evidence["evidence_id"]),
                    version_id=UUID(source["version_id"]),
                    source_id=UUID(source["source_id"]),
                    source_title=source["title"],
                    canonical_url=source["url"],
                    ordinal=evidence["ordinal"],
                    heading_path=tuple(evidence["heading_path"]),
                    page=evidence.get("page"),
                    anchor=evidence.get("anchor"),
                    text=text,
                    structured_content={},
                    topic_key=evidence["topic_key"],
                    scope={},
                    applicability=applicability,
                    model_revision="fixture-minilm-v1",
                    channel=RetrievalChannel.FULL_TEXT,
                    rank=rank,
                    ranking_value=float(rank),
                )
            )
    return tuple(evidence_items)


def _scope() -> RetrievalScope:
    return RetrievalScope(**_load_json("expected-answer.json")["scope"])


def _evidence_id(suffix: int) -> UUID:
    return UUID(f"30000000-0000-4000-8000-{suffix:012d}")


def _supported_candidate() -> StructuredAnswer:
    return StructuredAnswer(
        outcome=AnswerOutcome.ANSWER,
        segments=(
            AnswerSegment(
                kind=SegmentKind.EXPLANATION,
                text=(
                    "This exercise applies only to a fictional permit printed with both the "
                    "words TEST ONLY and the blue-lantern symbol. Continue only when the "
                    "fictional placard displays both TEST ONLY and a blue-lantern symbol."
                ),
                evidence_ids=(_evidence_id(1), _evidence_id(3)),
            ),
            AnswerSegment(
                kind=SegmentKind.EXPLANATION,
                text="The exercise is complete only after all five supported steps are finished.",
                evidence_ids=(_evidence_id(2),),
            ),
            AnswerSegment(
                kind=SegmentKind.STEP,
                text="Verify that both required marks appear on the fictional placard.",
                evidence_ids=(_evidence_id(4),),
            ),
            AnswerSegment(
                kind=SegmentKind.STEP,
                text=(
                    "Record the fixture reference code BL-204, then open the linked PDF to "
                    "continue with step 3."
                ),
                evidence_ids=(_evidence_id(4),),
            ),
            AnswerSegment(
                kind=SegmentKind.STEP,
                text="Enter Fixture Student and reference code BL-204.",
                evidence_ids=(_evidence_id(5),),
            ),
            AnswerSegment(
                kind=SegmentKind.STEP,
                text="Attach the sample image labeled BLUE-LANTERN-SAMPLE.",
                evidence_ids=(_evidence_id(5),),
            ),
            AnswerSegment(
                kind=SegmentKind.STEP,
                text=(
                    "Submit through the fixture-only submission route. Retain the synthetic "
                    "confirmation text TEST-COMPLETE."
                ),
                evidence_ids=(_evidence_id(6),),
            ),
        ),
    )


def _partial_candidate() -> StructuredAnswer:
    supported = _supported_candidate().segments
    return StructuredAnswer(
        outcome=AnswerOutcome.PARTIAL,
        segments=(
            supported[0],
            supported[2],
            supported[3],
            AnswerSegment(
                kind=SegmentKind.LIMITATION,
                text="I cannot verify the remaining steps from the available evidence.",
                evidence_ids=(),
            ),
        ),
        reason_code=ReasonCode.MISSING_EVIDENCE,
    )


def test_complete_corpus_validates_ordered_steps_and_all_source_citations() -> None:
    expected = _load_json("expected-answer.json")
    evidence = _retrieved_evidence()

    result = AnswerValidator().validate(
        _supported_candidate(),
        evidence=evidence,
        scope=_scope(),
    )

    assert result.accepted is True
    assert result.failures == ()
    assert [segment.kind for segment in result.answer.segments] == [
        SegmentKind.EXPLANATION,
        SegmentKind.EXPLANATION,
        SegmentKind.STEP,
        SegmentKind.STEP,
        SegmentKind.STEP,
        SegmentKind.STEP,
        SegmentKind.STEP,
    ]
    assert [
        segment.text for segment in result.answer.segments if segment.kind is SegmentKind.STEP
    ] == [
        "Verify that both required marks appear on the fictional placard.",
        "Record the fixture reference code BL-204, then open the linked PDF to continue with step 3.",
        "Enter Fixture Student and reference code BL-204.",
        "Attach the sample image labeled BLUE-LANTERN-SAMPLE.",
        "Submit through the fixture-only submission route. Retain the synthetic confirmation text TEST-COMPLETE.",
    ]
    assert {citation.evidence_id for citation in result.citations} == {
        item.evidence_id for item in evidence
    }
    evidence_by_id = {item.evidence_id: item for item in evidence}
    assert {
        str(evidence_by_id[citation.evidence_id].source_id) for citation in result.citations
    } == set(expected["supported_case"]["required_source_ids"])
    assert all(citation.url.startswith("https://www.pnw.edu/") for citation in result.citations)
    assert {citation.page for citation in result.citations} >= {1, 2}


def test_partial_answer_keeps_supported_steps_and_marks_unknown_remainder() -> None:
    expected = _load_json("expected-answer.json")
    partial = expected["partial_case"]
    evidence = tuple(
        item
        for item in _retrieved_evidence()
        if str(item.source_id) not in partial["ineligible_source_ids"]
    )

    result = AnswerValidator().validate(
        _partial_candidate(),
        evidence=evidence,
        scope=_scope(),
    )

    assert result.accepted is True
    assert result.answer.outcome is AnswerOutcome.PARTIAL
    assert result.answer.reason_code is ReasonCode.MISSING_EVIDENCE
    step_segments = [
        segment for segment in result.answer.segments if segment.kind is SegmentKind.STEP
    ]
    assert len(step_segments) == len(partial["supported_step_numbers"])
    assert result.answer.segments[-1].kind is SegmentKind.LIMITATION
    assert result.answer.segments[-1].evidence_ids == ()
    rendered_text = " ".join(segment.text for segment in result.answer.segments)
    assert "Fixture Student" not in rendered_text
    assert "BLUE-LANTERN-SAMPLE" not in rendered_text
    assert "TEST-COMPLETE" not in rendered_text
    assert {str(citation.evidence_id) for citation in result.citations} == {
        str(evidence_id)
        for segment in result.answer.segments
        for evidence_id in segment.evidence_ids
    }
    evidence_by_id = {item.evidence_id: item for item in evidence}
    assert all(
        str(evidence_by_id[citation.evidence_id].source_id) not in partial["ineligible_source_ids"]
        for citation in result.citations
    )


def test_partial_answer_rejects_a_later_step_without_its_evidence() -> None:
    evidence = tuple(item for item in _retrieved_evidence() if item.evidence_id != _evidence_id(5))
    partial = _partial_candidate()
    unsupported = StructuredAnswer(
        outcome=AnswerOutcome.PARTIAL,
        segments=(
            *partial.segments[:-1],
            AnswerSegment(
                kind=SegmentKind.STEP,
                text="Enter Fixture Student and reference code BL-204.",
                evidence_ids=(_evidence_id(4),),
            ),
            partial.segments[-1],
        ),
        reason_code=ReasonCode.MISSING_EVIDENCE,
    )

    result = AnswerValidator().validate(unsupported, evidence=evidence, scope=_scope())

    assert result.accepted is False
    assert ValidationFailureCode.UNSUPPORTED_CLAIM in result.failures
    assert result.answer.outcome is AnswerOutcome.UNABLE
    assert result.citations == ()
