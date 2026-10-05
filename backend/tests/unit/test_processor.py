"""Unit coverage for the production message-processing coordinator."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from app.academic.programs import (
    AcademicLimitation,
    AcademicScopeDecision,
    AcademicScopeOutcome,
    AcademicTopic,
)
from app.api.processor import RequestMessageProcessor, RetrievalResult
from app.api.schemas import AnswerEnvelope
from app.context import ContextField, ContextOptions, StudentContext
from app.generation.adapter import GenerationCancellation, GenerationRequest
from app.generation.safe_failure import NO_VERIFIED_REFERRAL_LIMITATION
from app.generation.schemas import (
    AnswerOutcome,
    AnswerSegment,
    ClarificationRequest,
    ReasonCode,
    SegmentKind,
    StructuredAnswer,
)
from app.retrieval.authorization import (
    AuthorizationDecision,
    AuthorizationFailureReason,
    AuthorizationPublishResult,
    EvidenceAuthorizationReference,
    FinalAnswerAuthorizer,
)
from app.retrieval.search import (
    EvidenceApplicability,
    RetrievalChannel,
    RetrievalScope,
    RetrievedEvidence,
)
from app.sessions import CancellationHandle, GenerationLease
from app.sessions.models import (
    ConversationMessage,
    MessageRole,
    RequestBuffer,
    SessionSnapshot,
)
from pydantic import SecretStr

pytestmark = pytest.mark.asyncio

EVIDENCE_ID = UUID("30000000-0000-4000-8000-000000000001")
VERSION_ID = UUID("20000000-0000-4000-8000-000000000001")


@dataclass
class _Retriever:
    evidence: tuple[RetrievedEvidence, ...]
    referrals: tuple[_Referral, ...] = ()
    failure_reason: ReasonCode | None = None
    related_evidence_ids: tuple[UUID, ...] = ()
    selected_context: StudentContext | None = None
    context_options: ContextOptions | None = None
    clarification: StructuredAnswer | None = None
    academic_scope: AcademicScopeDecision | None = None

    def retrieve(
        self,
        *,
        query: str,
        context: Mapping[str, str],
    ) -> RetrievalResult:
        assert query == "What is the fixture code?"
        assert context == {"campus": "Hammond"}
        return RetrievalResult(
            evidence=self.evidence,
            scope=_scope(),
            referrals=self.referrals,
            failure_reason=self.failure_reason,
            related_evidence_ids=self.related_evidence_ids,
            selected_context=self.selected_context,
            context_options=self.context_options,
            clarification=self.clarification,
            academic_scope=self.academic_scope,
        )


@dataclass(frozen=True)
class _Referral:
    office_name: str
    responsibilities: str
    contact_label: str
    contact_url: str
    evidence_block_id: UUID
    scope: dict[str, object]


class _Adapter:
    def __init__(self) -> None:
        self.requests: list[GenerationRequest] = []

    async def generate(
        self,
        request: GenerationRequest,
        *,
        cancellation: GenerationCancellation,
    ) -> StructuredAnswer:
        cancellation.raise_if_cancelled()
        self.requests.append(request)
        return StructuredAnswer(
            outcome=AnswerOutcome.ANSWER,
            segments=(
                AnswerSegment(
                    kind=SegmentKind.EXPLANATION,
                    text="The fixture code is BL-204.",
                    evidence_ids=(EVIDENCE_ID,),
                ),
            ),
        )


class _UnableAdapter(_Adapter):
    def __init__(self, reason_code: ReasonCode) -> None:
        super().__init__()
        self.reason_code = reason_code

    async def generate(
        self,
        request: GenerationRequest,
        *,
        cancellation: GenerationCancellation,
    ) -> StructuredAnswer:
        cancellation.raise_if_cancelled()
        self.requests.append(request)
        return StructuredAnswer(
            outcome=AnswerOutcome.UNABLE,
            segments=(
                AnswerSegment(
                    kind=SegmentKind.LIMITATION,
                    text="Untrusted provider limitation text.",
                ),
            ),
            reason_code=self.reason_code,
        )


class _RejectingAuthorizer:
    def __init__(self) -> None:
        self.references: tuple[EvidenceAuthorizationReference, ...] = ()

    def authorize_and_publish(
        self,
        references: tuple[EvidenceAuthorizationReference, ...],
        _publisher: object,
    ) -> AuthorizationPublishResult[SessionSnapshot]:
        self.references = references
        return AuthorizationPublishResult(
            decision=AuthorizationDecision(
                allowed=False,
                reason=AuthorizationFailureReason.SOURCE_WITHDRAWN,
            )
        )


def _scope() -> RetrievalScope:
    return RetrievalScope(
        topic="synthetic_procedure",
        institution="Purdue University Northwest",
        campus="Hammond",
    )


def _evidence() -> RetrievedEvidence:
    return RetrievedEvidence(
        evidence_id=EVIDENCE_ID,
        version_id=VERSION_ID,
        source_id=UUID("10000000-0000-4000-8000-000000000001"),
        source_title="Synthetic fixture",
        canonical_url="https://www.pnw.edu/__test__/fixture/",
        ordinal=0,
        heading_path=("Fixture", "Code"),
        page=None,
        anchor="code",
        text="The fixture code is BL-204.",
        structured_content={},
        topic_key="synthetic_procedure",
        scope={},
        applicability=EvidenceApplicability(
            topic="synthetic_procedure",
            institution="Purdue University Northwest",
            campus="Hammond",
            student_level=None,
            program=None,
            catalog_year=None,
            term=None,
            session=None,
        ),
        model_revision="sentence-transformers/all-MiniLM-L6-v2",
        channel=RetrievalChannel.FULL_TEXT,
        rank=1,
        ranking_value=1.0,
    )


def _lease() -> GenerationLease:
    return GenerationLease(
        token=SecretStr("test-token"),
        generation=1,
        cancellation=CancellationHandle(),
        buffer=RequestBuffer(
            [ConversationMessage(MessageRole.STUDENT, "What is the fixture code?")],
            {"campus": "Hammond"},
        ),
    )


async def test_processor_releases_only_validated_claims_and_derived_citations() -> None:
    adapter = _Adapter()
    processor = RequestMessageProcessor(
        retriever=_Retriever((_evidence(),)),
        primary=adapter,
    )

    result = await processor.process(_lease())

    assert result.outcome is AnswerOutcome.ANSWER
    assert result.context.campus == "Hammond"
    assert result.segments[0].citation_ids == (str(EVIDENCE_ID),)
    assert result.citations[0].id == str(EVIDENCE_ID)
    assert str(result.citations[0].url).endswith("#code")
    assert len(adapter.requests) == 1
    assert "What is the fixture code?" in adapter.requests[0].prompt


async def test_processor_fails_safely_without_calling_generation_when_no_evidence() -> None:
    adapter = _Adapter()
    processor = RequestMessageProcessor(retriever=_Retriever(()), primary=adapter)

    result = await processor.process(_lease())

    assert result.outcome is AnswerOutcome.UNABLE
    assert result.reason_code == "missing_evidence"
    assert result.citations == ()
    assert result.referral is None
    assert result.segments[-1].text == NO_VERIFIED_REFERRAL_LIMITATION
    assert adapter.requests == []


async def test_processor_returns_context_gate_clarification_without_generation() -> None:
    adapter = _Adapter()
    clarification = StructuredAnswer(
        outcome=AnswerOutcome.CLARIFICATION,
        clarification=ClarificationRequest(
            question="Which academic term should I use for this question?",
            fields=(ContextField.TERM,),
            options=("Fall", "Spring"),
        ),
        reason_code=ReasonCode.CONTEXT_REQUIRED,
    )
    processor = RequestMessageProcessor(
        retriever=_Retriever(
            (),
            selected_context=StudentContext(campus="Hammond"),
            context_options=ContextOptions(
                campus=("Hammond", "Westville"),
                term=("Fall", "Spring"),
            ),
            clarification=clarification,
        ),
        primary=adapter,
    )

    result = await processor.process(_lease())

    assert result.outcome is AnswerOutcome.CLARIFICATION
    assert result.reason_code is ReasonCode.CONTEXT_REQUIRED
    assert result.context.campus == "Hammond"
    assert result.context.term is None
    assert result.clarification is not None
    assert result.clarification.fields == (ContextField.TERM,)
    assert result.clarification.options == ("Fall", "Spring")
    assert adapter.requests == []


async def test_processor_replaces_provider_limitation_and_returns_verified_referral() -> None:
    evidence = _evidence()
    referral = _Referral(
        office_name="Office of the Registrar",
        responsibilities="Registration help",
        contact_label="Email the Registrar",
        contact_url="mailto:registrar@example.edu",
        evidence_block_id=evidence.evidence_id,
        scope={"campus": "Hammond"},
    )
    adapter = _UnableAdapter(ReasonCode.PERSONAL_CASE)
    processor = RequestMessageProcessor(
        retriever=_Retriever((evidence,), referrals=(referral,)),
        primary=adapter,
    )

    result = await processor.process(_lease())

    assert result.outcome is AnswerOutcome.UNABLE
    assert result.reason_code is ReasonCode.PERSONAL_CASE
    assert "student records" in result.segments[0].text
    assert "Untrusted provider" not in result.segments[0].text
    assert result.referral is not None
    assert result.referral.office_name == "Office of the Registrar"
    assert str(result.referral.contact_url) == "mailto:registrar@example.edu"
    assert result.referral.citation_ids == (str(EVIDENCE_ID),)
    assert tuple(citation.id for citation in result.citations) == (str(EVIDENCE_ID),)


async def test_processor_links_conflicting_sources_without_asserting_a_resolution() -> None:
    evidence = _evidence()
    adapter = _Adapter()
    processor = RequestMessageProcessor(
        retriever=_Retriever(
            (evidence,),
            failure_reason=ReasonCode.CONFLICT,
            related_evidence_ids=(evidence.evidence_id,),
        ),
        primary=adapter,
    )

    result = await processor.process(_lease())

    assert result.reason_code is ReasonCode.CONFLICT
    assert "conflicts" in result.segments[0].text
    assert result.segments[0].citation_ids == (str(EVIDENCE_ID),)
    assert tuple(citation.id for citation in result.citations) == (str(EVIDENCE_ID),)
    assert result.referral is None
    assert adapter.requests == []


async def test_processor_blocks_unconfirmed_current_availability_before_generation() -> None:
    evidence = _evidence()
    referral = _Referral(
        office_name="Office of the Registrar",
        responsibilities="Confirm current course availability",
        contact_label="Open the current schedule",
        contact_url="https://www.pnw.edu/schedule/",
        evidence_block_id=evidence.evidence_id,
        scope={"campus": "Hammond"},
    )
    adapter = _Adapter()
    decision = AcademicScopeDecision(
        topic=AcademicTopic.CURRENT_AVAILABILITY,
        outcome=AcademicScopeOutcome.LIMITED,
        selected_context=StudentContext(campus="Hammond"),
        evidence_ids=(evidence.evidence_id,),
        limitation=AcademicLimitation.CURRENT_AVAILABILITY_UNCONFIRMED,
        allows_general_guidance=True,
    )
    processor = RequestMessageProcessor(
        retriever=_Retriever(
            (evidence,),
            referrals=(referral,),
            academic_scope=decision,
        ),
        primary=adapter,
    )

    result = await processor.process(_lease())

    assert result.outcome is AnswerOutcome.UNABLE
    assert result.reason_code is ReasonCode.MISSING_EVIDENCE
    assert result.referral is not None
    assert result.referral.office_name == "Office of the Registrar"
    assert adapter.requests == []


async def test_final_authorization_replaces_withdrawn_answer_before_session_publish() -> None:
    authorizer = _RejectingAuthorizer()
    processor = RequestMessageProcessor(
        retriever=_Retriever((_evidence(),)),
        primary=_Adapter(),
        authorizer=cast(FinalAnswerAuthorizer, authorizer),
    )
    published: list[AnswerOutcome] = []
    now = datetime(2030, 1, 15, tzinfo=UTC)

    def publish(answer: AnswerEnvelope) -> SessionSnapshot:
        published.append(answer.outcome)
        return SessionSnapshot(
            created_at=now,
            last_student_message_at=now,
            expires_at=now + timedelta(minutes=30),
            generation=1,
            has_pending_request=False,
        )

    answer, _ = await processor.process_and_publish(_lease(), publisher=publish)

    assert answer.outcome is AnswerOutcome.UNABLE
    assert answer.reason_code is ReasonCode.SOURCE_UNAVAILABLE
    assert answer.citations == ()
    assert published == [AnswerOutcome.UNABLE]
    assert authorizer.references == (EvidenceAuthorizationReference(EVIDENCE_ID, VERSION_ID),)
