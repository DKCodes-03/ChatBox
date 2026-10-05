"""Integrated safe-failure coverage over the synthetic US2 adverse corpus."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import pytest
from app.api.processor import RequestMessageProcessor, RetrievalResult
from app.generation.adapter import GenerationCancellation, GenerationRequest
from app.generation.safe_failure import NO_VERIFIED_REFERRAL_LIMITATION
from app.generation.schemas import (
    AnswerOutcome,
    AnswerSegment,
    ReasonCode,
    SegmentKind,
    StructuredAnswer,
)
from app.retrieval.search import (
    EvidenceApplicability,
    RetrievalChannel,
    RetrievalScope,
    RetrievedEvidence,
)
from app.sessions import CancellationHandle, GenerationLease
from app.sessions.models import ConversationMessage, MessageRole, RequestBuffer
from bs4 import BeautifulSoup
from pydantic import SecretStr

pytestmark = pytest.mark.integration

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "corpus" / "us2"


def _load_json(name: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8")))


EXPECTED = _load_json("expected-results.json")
METADATA = _load_json("fixture-metadata.json")
CASES = cast(dict[str, dict[str, Any]], {item["id"]: item for item in EXPECTED["cases"]})
SOURCES = cast(
    dict[str, dict[str, Any]],
    {item["source_id"]: item for item in METADATA["sources"]},
)


@dataclass(frozen=True)
class _Referral:
    office_name: str
    responsibilities: str
    contact_label: str
    contact_url: str
    evidence_block_id: UUID
    scope: dict[str, object]


@dataclass
class _Retriever:
    expected_query: str
    result: RetrievalResult

    def retrieve(
        self,
        *,
        query: str,
        context: Mapping[str, str],
    ) -> RetrievalResult:
        assert query == self.expected_query
        assert context == {}
        return self.result


class _StaticAdapter:
    def __init__(self, answer: StructuredAnswer) -> None:
        self.answer = answer
        self.requests: list[GenerationRequest] = []

    async def generate(
        self,
        request: GenerationRequest,
        *,
        cancellation: GenerationCancellation,
    ) -> StructuredAnswer:
        cancellation.raise_if_cancelled()
        self.requests.append(request)
        return self.answer


def _scope(topic: str) -> RetrievalScope:
    return RetrievalScope(topic=topic, institution=EXPECTED["institution"])


def _source_for_topic(topic: str) -> dict[str, Any]:
    matches = [source for source in SOURCES.values() if source["applicability"]["topic"] == topic]
    assert len(matches) == 1
    return matches[0]


def _evidence(source: Mapping[str, Any]) -> tuple[RetrievedEvidence, ...]:
    fixture_path = FIXTURE_DIR / source["fixture_file"]
    soup = BeautifulSoup(fixture_path.read_text(encoding="utf-8"), "html.parser")
    applicability = EvidenceApplicability(**source["applicability"])
    items: list[RetrievedEvidence] = []
    for rank, item in enumerate(source["evidence"], start=1):
        section = soup.find(id=item["anchor"])
        assert section is not None
        text = " ".join(section.get_text(" ", strip=True).split())
        assert all(marker in text for marker in item["text_contains"])
        items.append(
            RetrievedEvidence(
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
                structured_content={},
                topic_key=item["topic_key"],
                scope={},
                applicability=applicability,
                model_revision="fixture-minilm-v1",
                channel=RetrievalChannel.FULL_TEXT,
                rank=rank,
                ranking_value=float(rank),
            )
        )
    return tuple(items)


def _referrals(source: Mapping[str, Any]) -> tuple[_Referral, ...]:
    return tuple(
        _Referral(
            office_name=item["office_name"],
            responsibilities=item["responsibilities"],
            contact_label=item["contact_label"],
            contact_url=item["contact_url"],
            evidence_block_id=UUID(item["evidence_block_id"]),
            scope=item["scope"],
        )
        for item in source["office_referrals"]
    )


def _lease(question: str) -> GenerationLease:
    return GenerationLease(
        token=SecretStr("synthetic-us2-session-token"),
        generation=1,
        cancellation=CancellationHandle(),
        buffer=RequestBuffer(
            [ConversationMessage(MessageRole.STUDENT, question)],
            {},
        ),
    )


def _unused_answer() -> StructuredAnswer:
    return StructuredAnswer(
        outcome=AnswerOutcome.ANSWER,
        segments=(
            AnswerSegment(
                kind=SegmentKind.EXPLANATION,
                text="This generated claim must never be released.",
                evidence_ids=(UUID("31000000-0000-4000-8000-000000000001"),),
            ),
        ),
    )


@pytest.mark.parametrize(
    "case_id",
    [
        "missing-evidence",
        "stale-source",
        "quarantined-source-and-contact",
        "source-outage",
    ],
)
async def test_ineligible_or_unavailable_sources_fail_without_generation(case_id: str) -> None:
    case = CASES[case_id]
    adapter = _StaticAdapter(_unused_answer())
    processor = RequestMessageProcessor(
        retriever=_Retriever(
            expected_query=case["question"],
            result=RetrievalResult(
                evidence=(),
                scope=_scope(case["topic"]),
                failure_reason=ReasonCode(case["expected_reason_code"]),
            ),
        ),
        primary=adapter,
    )

    result = await processor.process(_lease(case["question"]))

    assert result.outcome is AnswerOutcome.UNABLE
    assert result.reason_code == ReasonCode(case["expected_reason_code"])
    assert result.citations == ()
    assert result.referral is None
    assert adapter.requests == []
    rendered = " ".join(segment.text for segment in result.segments)
    for prohibited in case.get("prohibited_claims", []):
        assert prohibited not in rendered
    if case.get("must_say_no_verified_referral"):
        assert result.segments[-1].text == NO_VERIFIED_REFERRAL_LIMITATION


async def test_unresolved_conflict_withholds_both_conclusions_and_links_sources() -> None:
    case = CASES["unresolved-conflict"]
    conflict_sources = [
        source for source in SOURCES.values() if source["applicability"]["topic"] == case["topic"]
    ]
    evidence = tuple(item for source in conflict_sources for item in _evidence(source))
    adapter = _StaticAdapter(_unused_answer())
    processor = RequestMessageProcessor(
        retriever=_Retriever(
            expected_query=case["question"],
            result=RetrievalResult(
                evidence=evidence,
                scope=_scope(case["topic"]),
                failure_reason=ReasonCode.CONFLICT,
                related_evidence_ids=tuple(item.evidence_id for item in evidence),
            ),
        ),
        primary=adapter,
    )

    result = await processor.process(_lease(case["question"]))

    assert result.outcome is AnswerOutcome.UNABLE
    assert result.reason_code is ReasonCode.CONFLICT
    assert adapter.requests == []
    assert result.referral is None
    assert {citation.id for citation in result.citations} == set(
        case["expected_citation_evidence_ids"]
    )
    assert set(result.segments[0].citation_ids) == set(case["expected_citation_evidence_ids"])
    rendered = " ".join(segment.text for segment in result.segments)
    for prohibited in case["prohibited_claims"]:
        assert prohibited not in rendered


async def test_personal_case_replaces_provider_text_and_uses_only_verified_referral() -> None:
    case = CASES["personal-registration-case"]
    source = _source_for_topic(case["topic"])
    evidence = _evidence(source)
    untrusted = StructuredAnswer(
        outcome=AnswerOutcome.UNABLE,
        segments=(
            AnswerSegment(
                kind=SegmentKind.LIMITATION,
                text="I inspected your student record and fixed error ZX-17.",
            ),
        ),
        reason_code=ReasonCode.PERSONAL_CASE,
    )
    adapter = _StaticAdapter(untrusted)
    processor = RequestMessageProcessor(
        retriever=_Retriever(
            expected_query=case["question"],
            result=RetrievalResult(
                evidence=evidence,
                scope=_scope(case["topic"]),
                referrals=_referrals(source),
            ),
        ),
        primary=adapter,
    )

    result = await processor.process(_lease(case["question"]))

    assert result.outcome is AnswerOutcome.UNABLE
    assert result.reason_code is ReasonCode.PERSONAL_CASE
    assert len(adapter.requests) == 1
    assert result.referral is not None
    expected_referral = case["expected_referral"]
    assert result.referral.office_name == expected_referral["office_name"]
    assert result.referral.contact_label == expected_referral["contact_label"]
    assert str(result.referral.contact_url) == expected_referral["contact_url"]
    assert set(result.referral.citation_ids) == set(case["expected_citation_evidence_ids"])
    assert {citation.id for citation in result.citations} == set(
        case["expected_citation_evidence_ids"]
    )
    rendered = " ".join(segment.text for segment in result.segments)
    for prohibited in case["prohibited_claims"]:
        assert prohibited not in rendered


async def test_unsupported_generated_claim_is_replaced_and_gets_no_invented_referral() -> None:
    case = CASES["personal-registration-case"]
    source = _source_for_topic(case["topic"])
    evidence = _evidence(source)
    unsupported = StructuredAnswer(
        outcome=AnswerOutcome.ANSWER,
        segments=(
            AnswerSegment(
                kind=SegmentKind.EXPLANATION,
                text="You are eligible to register.",
                evidence_ids=(evidence[0].evidence_id,),
            ),
        ),
    )
    adapter = _StaticAdapter(unsupported)
    processor = RequestMessageProcessor(
        retriever=_Retriever(
            expected_query=case["question"],
            result=RetrievalResult(
                evidence=evidence,
                scope=_scope(case["topic"]),
                referrals=(),
            ),
        ),
        primary=adapter,
    )

    result = await processor.process(_lease(case["question"]))

    assert len(adapter.requests) == 1
    assert result.outcome is AnswerOutcome.UNABLE
    assert result.reason_code is ReasonCode.MISSING_EVIDENCE
    assert result.citations == ()
    assert result.referral is None
    assert result.segments[-1].text == NO_VERIFIED_REFERRAL_LIMITATION
    assert "You are eligible to register." not in " ".join(
        segment.text for segment in result.segments
    )
