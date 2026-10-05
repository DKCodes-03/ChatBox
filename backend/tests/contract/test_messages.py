"""Contract coverage for complete and partial supported-answer envelopes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from app.api.schemas import (
    AnswerEnvelope,
    AnswerOutcome,
    AnswerSegment,
    Citation,
    Context,
    ReasonCode,
    SegmentKind,
)
from app.config import Settings
from app.main import create_app
from app.sessions import GenerationLease, SessionManager
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import AnyHttpUrl, AnyUrl

pytestmark = pytest.mark.contract

ORIGIN = "https://testserver"
ORIGIN_HEADERS = {"Origin": ORIGIN}
FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "corpus" / "us1"


class _ReadinessProbe:
    def is_ready(self) -> bool:
        return True


@dataclass(frozen=True)
class _FixtureProcessor:
    answer: AnswerEnvelope

    async def process(self, _lease: GenerationLease) -> AnswerEnvelope:
        return self.answer


def _load_json(name: str) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8")),
    )


def _app(answer: AnswerEnvelope) -> FastAPI:
    return create_app(
        settings=Settings(public_origin=AnyHttpUrl(ORIGIN)),
        readiness_probe=_ReadinessProbe(),
        session_manager=SessionManager(auto_start=False),
        message_processor=_FixtureProcessor(answer),
    )


def _metadata_by_evidence_id(
    metadata: dict[str, Any],
) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    return {
        evidence["evidence_id"]: (source, evidence)
        for source in metadata["sources"]
        for evidence in source["evidence"]
    }


def _citation(
    evidence_id: str,
    metadata_index: dict[str, tuple[dict[str, Any], dict[str, Any]]],
) -> Citation:
    source, evidence = metadata_index[evidence_id]
    anchor = evidence.get("anchor")
    url = f"{source['url']}#{anchor}" if anchor is not None else source["url"]
    return Citation(
        id=evidence_id,
        source_title=source["title"],
        url=AnyUrl(url),
        section=" > ".join(evidence["heading_path"]),
        page=evidence.get("page"),
        version_id=UUID(source["version_id"]),
    )


def _answer_envelope(*, partial: bool) -> AnswerEnvelope:
    expected = _load_json("expected-answer.json")
    metadata = _load_json("fixture-metadata.json")
    evidence_index = _metadata_by_evidence_id(metadata)
    supported = expected["supported_case"]

    explanation = supported["required_explanation_claims"][0]
    segments = [
        AnswerSegment(
            kind=SegmentKind.EXPLANATION,
            text=explanation["claim"],
            citation_ids=tuple(explanation["evidence_ids"]),
        )
    ]
    steps = supported["ordered_steps"][:2] if partial else supported["ordered_steps"]
    if not partial:
        completion = supported["required_explanation_claims"][1]
        segments.append(
            AnswerSegment(
                kind=SegmentKind.EXPLANATION,
                text=completion["claim"],
                citation_ids=tuple(completion["evidence_ids"]),
            )
        )
    segments.extend(
        AnswerSegment(
            kind=SegmentKind.STEP,
            text=step["summary"],
            citation_ids=tuple(step["evidence_ids"]),
        )
        for step in steps
    )
    if partial:
        segments.append(
            AnswerSegment(
                kind=SegmentKind.LIMITATION,
                text="I cannot verify the remaining steps from the available evidence.",
                citation_ids=(),
            )
        )

    citation_ids = tuple(
        dict.fromkeys(citation_id for segment in segments for citation_id in segment.citation_ids)
    )
    now = datetime(2030, 1, 15, 12, 0, tzinfo=UTC)
    return AnswerEnvelope(
        outcome=AnswerOutcome.PARTIAL if partial else AnswerOutcome.ANSWER,
        segments=tuple(segments),
        citations=tuple(_citation(citation_id, evidence_index) for citation_id in citation_ids),
        context=Context(),
        clarification=None,
        referral=None,
        reason_code=ReasonCode.MISSING_EVIDENCE if partial else None,
        expires_at=now,
        server_time=now,
    )


def _post_question(answer: AnswerEnvelope) -> dict[str, Any]:
    expected = _load_json("expected-answer.json")
    with TestClient(_app(answer), base_url=ORIGIN) as client:
        created = client.post("/api/v1/sessions", json={}, headers=ORIGIN_HEADERS)
        assert created.status_code == 201
        response = client.post(
            "/api/v1/messages",
            json={"message": expected["question"]},
            headers=ORIGIN_HEADERS,
        )

    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"].split(", ")
    return cast(dict[str, Any], response.json())


def test_supported_answer_contract_preserves_step_order_and_complete_citations() -> None:
    expected = _load_json("expected-answer.json")
    metadata = _load_json("fixture-metadata.json")
    payload = _post_question(_answer_envelope(partial=False))

    assert payload["outcome"] == expected["supported_case"]["outcome"]
    step_segments = [segment for segment in payload["segments"] if segment["kind"] == "step"]
    assert [segment["text"] for segment in step_segments] == [
        step["summary"] for step in expected["supported_case"]["ordered_steps"]
    ]
    assert all(segment["citation_ids"] for segment in payload["segments"])

    citation_ids = {citation["id"] for citation in payload["citations"]}
    referenced_ids = {
        citation_id for segment in payload["segments"] for citation_id in segment["citation_ids"]
    }
    assert citation_ids == referenced_ids
    assert citation_ids == {
        evidence["evidence_id"] for source in metadata["sources"] for evidence in source["evidence"]
    }

    evidence_index = _metadata_by_evidence_id(metadata)
    cited_source_ids = {evidence_index[citation_id][0]["source_id"] for citation_id in citation_ids}
    assert cited_source_ids == set(expected["supported_case"]["required_source_ids"])
    assert all(
        citation["source_title"] and citation["url"].startswith("https://www.pnw.edu/")
        for citation in payload["citations"]
    )
    assert any(citation.get("section") for citation in payload["citations"])
    assert {citation.get("page") for citation in payload["citations"]} >= {1, 2}
    assert payload["reason_code"] is None


def test_partial_answer_contract_separates_supported_and_unknown_claims() -> None:
    expected = _load_json("expected-answer.json")
    metadata = _load_json("fixture-metadata.json")
    payload = _post_question(_answer_envelope(partial=True))
    partial = expected["partial_case"]

    assert payload["outcome"] == partial["outcome"]
    assert payload["reason_code"] == partial["required_reason_code"]
    assert [segment["kind"] for segment in payload["segments"]] == [
        "explanation",
        "step",
        "step",
        "limitation",
    ]
    supported_steps = [segment for segment in payload["segments"] if segment["kind"] == "step"]
    assert [segment["text"] for segment in supported_steps] == [
        expected["supported_case"]["ordered_steps"][number - 1]["summary"]
        for number in partial["supported_step_numbers"]
    ]
    limitation = payload["segments"][-1]
    assert limitation["citation_ids"] == []

    rendered_text = " ".join(segment["text"] for segment in payload["segments"])
    assert (
        expected["supported_case"]["required_explanation_claims"][1]["claim"] not in rendered_text
    )
    for number in partial["withheld_step_numbers"]:
        assert (
            expected["supported_case"]["ordered_steps"][number - 1]["summary"] not in rendered_text
        )

    evidence_index = _metadata_by_evidence_id(metadata)
    cited_source_ids = {
        evidence_index[citation["id"]][0]["source_id"] for citation in payload["citations"]
    }
    assert cited_source_ids.isdisjoint(partial["ineligible_source_ids"])
    assert "evidence_ids" not in json.dumps(payload)
    assert "qualification_id" not in json.dumps(payload)
