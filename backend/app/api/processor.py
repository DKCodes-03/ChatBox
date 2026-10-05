"""Production request coordination from transient session input to validated output."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from pydantic import AnyUrl
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.academic.authorization import AcademicAuthorization, authorize_academic_evidence
from app.academic.prerequisites import (
    CoursePrerequisites,
    InvalidPrerequisiteDataError,
    PrerequisiteRetrievalError,
)
from app.academic.programs import (
    AcademicLimitation,
    AcademicScopeDecision,
    AcademicScopeOutcome,
    InvalidAcademicScopeError,
)
from app.api.errors import SafeAPIError, SafeErrorCode
from app.api.schemas import (
    AnswerEnvelope,
    Citation,
    Clarification,
    Context,
    Referral,
)
from app.api.schemas import (
    AnswerSegment as PublicAnswerSegment,
)
from app.config import ProviderMode, Settings
from app.context import (
    ContextField,
    ContextOptions,
    ContextValidationError,
    StudentContext,
)
from app.generation.adapter import LLMAdapter
from app.generation.errors import (
    GenerationFailureError,
    generate_with_optional_fallback,
)
from app.generation.gemini import GeminiAdapter
from app.generation.local import LocalLlamaAdapter
from app.generation.prompt import PromptError, build_generation_request
from app.generation.safe_failure import (
    OfficeReferralRecord,
    SafeFailureResult,
    VerifiedOfficeReferral,
    assemble_safe_failure,
)
from app.generation.schemas import (
    AnswerOutcome,
    ReasonCode,
    StructuredAnswer,
)
from app.generation.validator import AnswerValidator, EvidenceCitation
from app.models.guidance import OfficeReferral
from app.retrieval.authorization import (
    AuthorizationError,
    AuthorizationFailureReason,
    EvidenceAuthorizationReference,
    FinalAnswerAuthorizer,
)
from app.retrieval.context_gate import ContextGate
from app.retrieval.embeddings import (
    EMBEDDING_DIMENSIONS,
    EmbeddingError,
    MiniLMEmbeddingService,
    TemporaryQueryVector,
)
from app.retrieval.search import (
    EvidenceSearch,
    RetrievalCandidates,
    RetrievalError,
    RetrievalScope,
    RetrievedEvidence,
)
from app.sessions import GenerationLease
from app.sessions.models import SessionSnapshot

INSTITUTION = "Purdue University Northwest"
MAX_TOPIC_QUERY_TERMS = 24
_TOPIC_QUERY_TERM_RE = re.compile(r"[A-Za-z0-9]+")

TOPIC_QUERY = text(
    """
    SELECT evidence.topic_key
    FROM evidence_blocks AS evidence
    JOIN source_versions AS version ON version.id = evidence.version_id
    JOIN sources AS source ON source.id = version.source_id
    JOIN embeddings AS embedding ON embedding.block_id = evidence.id
    WHERE source.status = 'eligible'
      AND version.extraction_status = 'complete'
      AND embedding.model_revision = :model_revision
      AND embedding.dimensions = :embedding_dimensions
      AND (
          CAST(:topic_hint AS text) IS NULL
          OR evidence.topic_key ILIKE CAST(:topic_hint AS text)
      )
      AND to_tsvector(
            'english',
            concat_ws(' ', array_to_string(evidence.heading_path, ' '), evidence.text)
          ) @@ websearch_to_tsquery('english', :query)
      AND EXISTS (
          SELECT 1
          FROM qualifications AS qualification
          WHERE qualification.version_id = version.id
            AND qualification.status = 'passed'
            AND qualification.valid_until > CURRENT_TIMESTAMP
            AND NOT EXISTS (
                SELECT 1
                FROM qualifications AS later
                WHERE later.version_id = version.id
                  AND later.checked_at > qualification.checked_at
            )
            AND NOT EXISTS (
                SELECT 1
                FROM qualifications AS same_time_failure
                WHERE same_time_failure.version_id = version.id
                  AND same_time_failure.checked_at = qualification.checked_at
                  AND same_time_failure.status <> 'passed'
            )
      )
    GROUP BY evidence.topic_key
    ORDER BY max(
        ts_rank_cd(
          to_tsvector(
            'english',
            concat_ws(' ', array_to_string(evidence.heading_path, ' '), evidence.text)
          ),
          websearch_to_tsquery('english', :query)
        )
      ) DESC,
      evidence.topic_key ASC
    LIMIT 1
    """
)

BLOCKED_TOPIC_QUERY = text(
    """
    SELECT evidence.topic_key
    FROM evidence_blocks AS evidence
    JOIN source_versions AS version ON version.id = evidence.version_id
    JOIN sources AS source ON source.id = version.source_id
    JOIN embeddings AS embedding ON embedding.block_id = evidence.id
    WHERE embedding.model_revision = :model_revision
      AND (
          CAST(:topic_hint AS text) IS NULL
          OR evidence.topic_key ILIKE CAST(:topic_hint AS text)
      )
      AND to_tsvector(
            'english',
            concat_ws(' ', array_to_string(evidence.heading_path, ' '), evidence.text)
          ) @@ websearch_to_tsquery('english', :query)
    GROUP BY evidence.topic_key
    ORDER BY max(
        ts_rank_cd(
          to_tsvector(
            'english',
            concat_ws(' ', array_to_string(evidence.heading_path, ' '), evidence.text)
          ),
          websearch_to_tsquery('english', :query)
        )
      ) DESC,
      evidence.topic_key ASC
    LIMIT 1
    """
)

CONTEXT_PROFILES_QUERY = text(
    """
    SELECT DISTINCT
        applicability.campus,
        applicability.term,
        NULLIF(btrim(evidence.scope ->> 'year'), '') AS year,
        applicability.session,
        applicability.program,
        applicability.student_level,
        applicability.catalog_year
    FROM evidence_blocks AS evidence
    JOIN source_versions AS version ON version.id = evidence.version_id
    JOIN sources AS source ON source.id = version.source_id
    JOIN embeddings AS embedding ON embedding.block_id = evidence.id
    JOIN applicability ON applicability.version_id = version.id
      AND evidence.id = ANY(applicability.evidence_block_ids)
    WHERE evidence.topic_key = :topic
      AND applicability.topic = :topic
      AND applicability.institution = :institution
      AND source.status = 'eligible'
      AND version.extraction_status = 'complete'
      AND (version.effective_from IS NULL OR version.effective_from <= CURRENT_TIMESTAMP)
      AND (version.effective_to IS NULL OR version.effective_to > CURRENT_TIMESTAMP)
      AND embedding.model_revision = :model_revision
      AND embedding.dimensions = :embedding_dimensions
      AND to_tsvector(
            'english',
            concat_ws(' ', array_to_string(evidence.heading_path, ' '), evidence.text)
          ) @@ websearch_to_tsquery('english', :query)
      AND EXISTS (
          SELECT 1
          FROM qualifications AS qualification
          WHERE qualification.version_id = version.id
            AND qualification.status = 'passed'
            AND qualification.valid_until > CURRENT_TIMESTAMP
            AND NOT EXISTS (
                SELECT 1
                FROM qualifications AS later
                WHERE later.version_id = version.id
                  AND later.checked_at > qualification.checked_at
            )
            AND NOT EXISTS (
                SELECT 1
                FROM qualifications AS same_time_failure
                WHERE same_time_failure.version_id = version.id
                  AND same_time_failure.checked_at = qualification.checked_at
                  AND same_time_failure.status <> 'passed'
            )
      )
      AND NOT EXISTS (
          SELECT 1
          FROM conflict_evidence_blocks AS conflict_block
          JOIN conflicts AS conflict ON conflict.id = conflict_block.conflict_id
          WHERE conflict_block.evidence_block_id = evidence.id
            AND conflict.status = 'unresolved'
      )
    ORDER BY
        applicability.campus NULLS FIRST,
        applicability.term NULLS FIRST,
        year NULLS FIRST,
        applicability.session NULLS FIRST,
        applicability.program NULLS FIRST,
        applicability.student_level NULLS FIRST,
        applicability.catalog_year NULLS FIRST
    """
)

FAILURE_REASON_QUERY = text(
    """
    WITH candidates AS (
        SELECT
            source.status AS source_status,
            version.effective_from,
            version.effective_to,
            version.extraction_status,
            latest_qualification.status AS qualification_status,
            latest_qualification.valid_until
        FROM evidence_blocks AS evidence
        JOIN source_versions AS version ON version.id = evidence.version_id
        JOIN sources AS source ON source.id = version.source_id
        JOIN applicability ON applicability.version_id = version.id
          AND evidence.id = ANY(applicability.evidence_block_ids)
        LEFT JOIN LATERAL (
            SELECT qualification.status, qualification.valid_until
            FROM qualifications AS qualification
            WHERE qualification.version_id = version.id
            ORDER BY qualification.checked_at DESC, qualification.id DESC
            LIMIT 1
        ) AS latest_qualification ON TRUE
        WHERE evidence.topic_key = :topic
          AND applicability.topic = :topic
          AND applicability.institution = :institution
          AND applicability.campus IS NOT DISTINCT FROM :campus
          AND applicability.student_level IS NOT DISTINCT FROM :student_level
          AND applicability.program IS NOT DISTINCT FROM :program
          AND applicability.catalog_year IS NOT DISTINCT FROM :catalog_year
          AND applicability.term IS NOT DISTINCT FROM :term
          AND applicability.session IS NOT DISTINCT FROM :session
          AND NULLIF(btrim(evidence.scope ->> 'year'), '') IS NOT DISTINCT FROM :year
    )
    SELECT CASE
        WHEN EXISTS (
            SELECT 1
            FROM candidates
            WHERE source_status = 'stale'
               OR (
                    source_status = 'eligible'
                    AND (
                        effective_to <= CURRENT_TIMESTAMP
                        OR (
                            qualification_status = 'passed'
                            AND valid_until <= CURRENT_TIMESTAMP
                        )
                    )
               )
        ) THEN 'expired_source'
        WHEN EXISTS (SELECT 1 FROM candidates) THEN 'source_unavailable'
        ELSE 'missing_evidence'
    END
    """
)


class EvidenceRetriever(Protocol):
    """Retrieve a bounded evidence set and the exact scope used to authorize it."""

    def retrieve(
        self,
        *,
        query: str,
        context: Mapping[str, str],
    ) -> RetrievalResult: ...


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """Evidence and related metadata selected in one retrieval transaction."""

    evidence: tuple[RetrievedEvidence, ...]
    scope: RetrievalScope
    referrals: tuple[OfficeReferralRecord, ...] = ()
    failure_reason: ReasonCode | None = None
    related_evidence_ids: tuple[UUID, ...] = ()
    selected_context: StudentContext | None = None
    context_options: ContextOptions | None = None
    clarification: StructuredAnswer | None = None
    academic_scope: AcademicScopeDecision | None = None
    prerequisites: tuple[CoursePrerequisites, ...] = ()


class QueryEmbeddingService(Protocol):
    """Create one request-scoped vector that is erased on context exit."""

    def temporary_query_vector(
        self,
        query: str,
    ) -> AbstractContextManager[TemporaryQueryVector]: ...


class RuntimeEvidenceRetriever:
    """Use the runtime database role and transient MiniLM query vectors."""

    def __init__(
        self,
        *,
        engine: Engine,
        embeddings: QueryEmbeddingService,
        model_revision: str,
        maximum_evidence: int,
        context_gate: ContextGate | None = None,
    ) -> None:
        if maximum_evidence < 1 or maximum_evidence > 8:
            raise ValueError("retrieval evidence limit must be between one and eight")
        self._engine = engine
        self._session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        self._embeddings = embeddings
        self._model_revision = model_revision
        self._maximum_evidence = maximum_evidence
        self._context_gate = context_gate or ContextGate()

    @classmethod
    def from_settings(cls, settings: Settings) -> RuntimeEvidenceRetriever:
        embeddings: QueryEmbeddingService
        if settings.synthetic_test_mode:
            from app.testing.embedding import DeterministicEmbeddingService

            embeddings = DeterministicEmbeddingService()
        else:
            embeddings = MiniLMEmbeddingService.from_settings(settings)
        engine = create_engine(
            settings.database_url(),
            connect_args={"connect_timeout": 3, "options": "-c statement_timeout=5000"},
            echo=False,
            hide_parameters=True,
            pool_pre_ping=True,
        )
        return cls(
            engine=engine,
            embeddings=embeddings,
            model_revision=settings.embedding_model,
            maximum_evidence=settings.retrieval_max_evidence_blocks,
        )

    def close(self) -> None:
        self._engine.dispose()

    @property
    def engine(self) -> Engine:
        """Share the runtime pool with short final-authorization transactions."""

        return self._engine

    def retrieve(
        self,
        *,
        query: str,
        context: Mapping[str, str],
    ) -> RetrievalResult:
        try:
            with self._session_factory() as database_session:
                topic = self._resolve_topic(database_session, query)
                if topic is None:
                    blocked_topic = self._resolve_blocked_topic(database_session, query)
                    if blocked_topic is not None:
                        blocked_scope = self._scope(blocked_topic, context)
                        return RetrievalResult(
                            evidence=(),
                            scope=blocked_scope,
                            failure_reason=self._failure_reason(database_session, blocked_scope),
                        )
                if topic is None:
                    topic = self._resolve_topic(database_session, query, broad=True)
                if topic is None:
                    blocked_topic = self._resolve_blocked_topic(
                        database_session,
                        query,
                        broad=True,
                    )
                    if blocked_topic is not None:
                        blocked_scope = self._scope(blocked_topic, context)
                        return RetrievalResult(
                            evidence=(),
                            scope=blocked_scope,
                            failure_reason=self._failure_reason(database_session, blocked_scope),
                        )
                if topic is None:
                    return RetrievalResult(
                        evidence=(),
                        scope=self._scope("unresolved", context),
                        failure_reason=ReasonCode.MISSING_EVIDENCE,
                    )
                profiles = self._context_profiles(database_session, query, topic)
                decision = self._context_gate.evaluate(context=context, profiles=profiles)
                selected_context = decision.selected_context
                context_options = decision.options
                scope = self._scope(topic, selected_context.as_mapping())
                if decision.requires_clarification:
                    return RetrievalResult(
                        evidence=(),
                        scope=scope,
                        selected_context=selected_context,
                        context_options=context_options,
                        clarification=decision.clarification,
                    )
                with self._embeddings.temporary_query_vector(query) as query_vector:
                    search = EvidenceSearch(
                        database_session,
                        model_revision=self._model_revision,
                    )
                    candidates = search.search(
                        query=query,
                        query_vector=query_vector,
                        scope=scope,
                    )
                    evidence = self._fuse(candidates)
                    conflicted_evidence = (
                        ()
                        if evidence
                        else self._fuse(
                            search.search(
                                query=query,
                                query_vector=query_vector,
                                scope=scope,
                                include_unresolved_conflicts=True,
                            )
                        )
                    )
                if conflicted_evidence:
                    return RetrievalResult(
                        evidence=conflicted_evidence,
                        scope=scope,
                        failure_reason=ReasonCode.CONFLICT,
                        related_evidence_ids=tuple(
                            item.evidence_id for item in conflicted_evidence
                        ),
                        selected_context=selected_context,
                        context_options=context_options,
                    )
                academic = AcademicAuthorization()
                if evidence:
                    try:
                        academic = authorize_academic_evidence(
                            database_session,
                            question=query,
                            context=selected_context,
                            evidence=evidence,
                        )
                    except (InvalidAcademicScopeError, InvalidPrerequisiteDataError, ValueError):
                        academic = AcademicAuthorization(failure_reason=ReasonCode.MISSING_EVIDENCE)
                failure_reason = (
                    academic.failure_reason
                    if academic.failure_reason is not None
                    else None
                    if evidence
                    else self._failure_reason(database_session, scope)
                )
                return RetrievalResult(
                    evidence=evidence,
                    scope=scope,
                    referrals=self._referrals(database_session, evidence),
                    failure_reason=failure_reason,
                    selected_context=selected_context,
                    context_options=context_options,
                    academic_scope=academic.decision,
                    prerequisites=academic.prerequisites,
                )
        except (
            ContextValidationError,
            EmbeddingError,
            PrerequisiteRetrievalError,
            RetrievalError,
            SQLAlchemyError,
        ):
            raise SafeAPIError(SafeErrorCode.PROCESSING_UNAVAILABLE) from None

    @staticmethod
    def _referrals(
        database_session: Session,
        evidence: Sequence[RetrievedEvidence],
    ) -> tuple[OfficeReferral, ...]:
        evidence_ids = tuple(item.evidence_id for item in evidence)
        if not evidence_ids:
            return ()
        statement = (
            select(OfficeReferral)
            .where(OfficeReferral.evidence_block_id.in_(evidence_ids))
            .order_by(
                OfficeReferral.office_name.asc(),
                OfficeReferral.contact_url.asc(),
                OfficeReferral.id.asc(),
            )
        )
        return tuple(database_session.scalars(statement))

    def _resolve_topic(
        self,
        database_session: Session,
        query: str,
        *,
        broad: bool = False,
    ) -> str | None:
        try:
            value = database_session.execute(
                TOPIC_QUERY,
                {
                    "query": _topic_search_query(query) if broad else query,
                    "model_revision": self._model_revision,
                    "embedding_dimensions": EMBEDDING_DIMENSIONS,
                    "topic_hint": _topic_hint(query),
                },
            ).scalar_one_or_none()
        except SQLAlchemyError:
            raise SafeAPIError(SafeErrorCode.PROCESSING_UNAVAILABLE) from None
        return value if isinstance(value, str) and value.strip() else None

    def _resolve_blocked_topic(
        self,
        database_session: Session,
        query: str,
        *,
        broad: bool = False,
    ) -> str | None:
        value = database_session.execute(
            BLOCKED_TOPIC_QUERY,
            {
                "query": _topic_search_query(query) if broad else query,
                "model_revision": self._model_revision,
                "topic_hint": _topic_hint(query),
            },
        ).scalar_one_or_none()
        return value if isinstance(value, str) and value.strip() else None

    def _context_profiles(
        self,
        database_session: Session,
        query: str,
        topic: str,
    ) -> tuple[Mapping[str, object], ...]:
        rows = database_session.execute(
            CONTEXT_PROFILES_QUERY,
            {
                "query": _topic_search_query(query),
                "topic": topic,
                "institution": INSTITUTION,
                "model_revision": self._model_revision,
                "embedding_dimensions": EMBEDDING_DIMENSIONS,
            },
        ).mappings()
        return tuple(dict(row) for row in rows)

    @staticmethod
    def _failure_reason(database_session: Session, scope: RetrievalScope) -> ReasonCode:
        value = database_session.execute(
            FAILURE_REASON_QUERY,
            {
                "topic": scope.topic,
                "institution": scope.institution,
                "campus": scope.campus,
                "student_level": scope.student_level,
                "program": scope.program,
                "catalog_year": scope.catalog_year,
                "term": scope.term,
                "session": scope.session,
                "year": scope.year,
            },
        ).scalar_one()
        try:
            return ReasonCode(value)
        except (TypeError, ValueError):
            return ReasonCode.MISSING_EVIDENCE

    @staticmethod
    def _scope(topic: str, context: Mapping[str, str]) -> RetrievalScope:
        return RetrievalScope(
            topic=topic,
            institution=INSTITUTION,
            campus=context.get(ContextField.CAMPUS.value),
            student_level=context.get(ContextField.STUDENT_LEVEL.value),
            program=context.get(ContextField.PROGRAM.value),
            catalog_year=context.get(ContextField.CATALOG_YEAR.value),
            term=context.get(ContextField.TERM.value),
            session=context.get(ContextField.SESSION.value),
            year=context.get(ContextField.YEAR.value),
        )

    def _fuse(self, candidates: RetrievalCandidates) -> tuple[RetrievedEvidence, ...]:
        return candidates.select(maximum_evidence=self._maximum_evidence)


def _topic_search_query(query: str) -> str:
    terms = _TOPIC_QUERY_TERM_RE.findall(query)[:MAX_TOPIC_QUERY_TERMS]
    return " OR ".join(terms) if terms else query


def _topic_hint(query: str) -> str | None:
    terms = _TOPIC_QUERY_TERM_RE.findall(query.casefold())
    normalized = " ".join(terms)
    if "admission" in terms or "admit" in terms or "admitted" in terms:
        return "%admission%"
    if "graduation" in terms or "graduating" in terms:
        return "%graduation%"
    if "plan of study" in normalized:
        return "%plan_of_study%"
    if "prerequisite" in normalized or "corequisite" in normalized:
        return "%prerequisite%"
    if "available" in normalized or "availability" in normalized:
        return "%availability%"
    if "program" in normalized and ("exist" in normalized or "offered" in normalized):
        return "%program%"
    return None


class RequestMessageProcessor:
    """Coordinate retrieval, generation, validation, and public response assembly."""

    def __init__(
        self,
        *,
        retriever: EvidenceRetriever,
        primary: LLMAdapter,
        fallback: LLMAdapter | None = None,
        validator: AnswerValidator | None = None,
        authorizer: FinalAnswerAuthorizer | None = None,
        generation_concurrency: int = 4,
    ) -> None:
        if generation_concurrency < 1 or generation_concurrency > 4:
            raise ValueError("generation concurrency must be between one and four")
        self._retriever = retriever
        self._primary = primary
        self._fallback = fallback
        self._validator = validator or AnswerValidator()
        self._authorizer = authorizer
        self._generation_slots = asyncio.Semaphore(generation_concurrency)

    async def process(self, lease: GenerationLease) -> AnswerEnvelope:
        lease.cancellation.raise_if_cancelled()
        messages, context = lease.buffer.snapshot()
        query = messages[-1].content
        retrieval = await asyncio.to_thread(
            self._retriever.retrieve,
            query=query,
            context=context,
        )
        lease.cancellation.raise_if_cancelled()
        evidence = retrieval.evidence
        scope = retrieval.scope
        answer_context = (
            retrieval.selected_context.as_mapping()
            if retrieval.selected_context is not None
            else dict(context)
        )
        if retrieval.clarification is not None:
            return _public_envelope(retrieval.clarification, (), answer_context)
        safe_failure: SafeFailureResult | None

        academic_failure = _academic_failure_reason(retrieval.academic_scope)

        if retrieval.failure_reason is not None or academic_failure is not None or not evidence:
            safe_failure = assemble_safe_failure(
                retrieval.failure_reason or academic_failure or ReasonCode.MISSING_EVIDENCE,
                evidence=evidence,
                scope=scope,
                referral_candidates=retrieval.referrals,
                related_evidence_ids=retrieval.related_evidence_ids,
            )
        else:
            try:
                request = build_generation_request(
                    messages=messages,
                    context=answer_context,
                    eligible_evidence=evidence,
                )
            except PromptError:
                raise SafeAPIError(SafeErrorCode.PROCESSING_UNAVAILABLE) from None

            try:
                async with self._generation_slots:
                    lease.cancellation.raise_if_cancelled()
                    candidate = await generate_with_optional_fallback(
                        self._primary,
                        request,
                        cancellation=lease.cancellation,
                        fallback=self._fallback,
                    )
            except GenerationFailureError as failure:
                del failure
                safe_failure = assemble_safe_failure(
                    ReasonCode.PROCESSING_UNAVAILABLE,
                    evidence=evidence,
                    scope=scope,
                    referral_candidates=retrieval.referrals,
                )
            else:
                safe_failure = (
                    assemble_safe_failure(
                        candidate.reason_code or ReasonCode.MISSING_EVIDENCE,
                        evidence=evidence,
                        scope=scope,
                        referral_candidates=retrieval.referrals,
                        related_evidence_ids=retrieval.related_evidence_ids,
                    )
                    if candidate.outcome is AnswerOutcome.UNABLE
                    else None
                )

        lease.cancellation.raise_if_cancelled()
        if safe_failure is not None:
            return _safe_failure_envelope(safe_failure, answer_context)

        explicit_context = {
            ContextField(key): value
            for key, value in answer_context.items()
            if key in {field.value for field in ContextField}
        }
        known_context_values = (
            {
                field: retrieval.context_options.for_field(field)
                for field in ContextField
                if retrieval.context_options.for_field(field)
            }
            if retrieval.context_options is not None
            else None
        )
        result = self._validator.validate(
            candidate,
            evidence=evidence,
            scope=scope,
            explicit_context=explicit_context,
            known_context_values=known_context_values,
        )
        lease.cancellation.raise_if_cancelled()
        if not result.accepted:
            safe_failure = assemble_safe_failure(
                ReasonCode.MISSING_EVIDENCE,
                evidence=evidence,
                scope=scope,
                referral_candidates=retrieval.referrals,
            )
            return _safe_failure_envelope(safe_failure, answer_context)
        return _public_envelope(result.answer, result.citations, answer_context)

    async def process_and_publish(
        self,
        lease: GenerationLease,
        *,
        publisher: Callable[[AnswerEnvelope], SessionSnapshot],
    ) -> tuple[AnswerEnvelope, SessionSnapshot]:
        """Authorize cited evidence and publish while governance source locks remain held."""

        if not callable(publisher):
            raise TypeError("publisher must be callable")
        answer = await self.process(lease)
        references = tuple(
            EvidenceAuthorizationReference(
                evidence_id=UUID(citation.id),
                version_id=citation.version_id,
            )
            for citation in answer.citations
        )
        if self._authorizer is None or not references:
            return answer, publisher(answer)
        try:
            published = await asyncio.to_thread(
                self._authorizer.authorize_and_publish,
                references,
                lambda: publisher(answer),
            )
        except (AuthorizationError, TypeError, ValueError):
            raise SafeAPIError(SafeErrorCode.PROCESSING_UNAVAILABLE) from None
        if published.decision.allowed:
            if published.value is None:
                raise SafeAPIError(SafeErrorCode.PROCESSING_UNAVAILABLE)
            return answer, published.value

        limitation = _authorization_limitation(
            published.decision.reason,
            context=answer.context,
        )
        return limitation, publisher(limitation)

    def close(self) -> None:
        close = getattr(self._retriever, "close", None)
        if callable(close):
            close()


def build_runtime_message_processor(settings: Settings) -> RequestMessageProcessor:
    """Construct the concrete processor used by the deployed FastAPI application."""

    retriever = RuntimeEvidenceRetriever.from_settings(settings)
    try:
        authorizer = FinalAnswerAuthorizer(retriever.engine)
        fallback = (
            LocalLlamaAdapter.from_settings(settings)
            if settings.local_inference_enabled and settings.llm_provider is ProviderMode.GEMINI
            else None
        )
        primary: LLMAdapter
        if settings.llm_provider is ProviderMode.LOCAL:
            primary = LocalLlamaAdapter.from_settings(settings)
        else:
            primary = GeminiAdapter.from_settings(settings)
        return RequestMessageProcessor(
            retriever=retriever,
            primary=primary,
            fallback=fallback,
            authorizer=authorizer,
            generation_concurrency=settings.generation_max_concurrency,
        )
    except Exception:
        retriever.close()
        raise


def _public_envelope(
    answer: StructuredAnswer,
    citations: Sequence[EvidenceCitation],
    context: Mapping[str, str],
    *,
    referral: VerifiedOfficeReferral | None = None,
    limitation_citation_ids: Sequence[UUID] = (),
) -> AnswerEnvelope:
    now = datetime.now(UTC)
    clarification = answer.clarification
    return AnswerEnvelope(
        outcome=answer.outcome,
        segments=tuple(
            PublicAnswerSegment(
                kind=segment.kind,
                text=segment.text,
                citation_ids=tuple(
                    str(evidence_id)
                    for evidence_id in (
                        tuple(segment.evidence_ids)
                        + (tuple(limitation_citation_ids) if index == 0 else ())
                    )
                ),
            )
            for index, segment in enumerate(answer.segments)
        ),
        citations=tuple(
            Citation(
                id=item.id,
                source_title=item.source_title,
                url=AnyUrl(item.url),
                section=item.section,
                page=item.page,
                version_id=item.version_id,
            )
            for item in citations
        ),
        context=Context(**context),
        clarification=(
            Clarification(
                question=clarification.question,
                fields=clarification.fields,
                options=clarification.options or None,
            )
            if clarification is not None
            else None
        ),
        referral=(
            Referral(
                office_name=referral.office_name,
                contact_label=referral.contact_label,
                contact_url=AnyUrl(referral.contact_url),
                citation_ids=tuple(str(evidence_id) for evidence_id in referral.evidence_ids),
            )
            if referral is not None
            else None
        ),
        reason_code=answer.reason_code,
        expires_at=now,
        server_time=now,
    )


def _safe_failure_envelope(
    result: SafeFailureResult,
    context: Mapping[str, str],
) -> AnswerEnvelope:
    return _public_envelope(
        result.answer,
        result.citations,
        context,
        referral=result.referral,
        limitation_citation_ids=result.related_citation_ids,
    )


def _academic_failure_reason(decision: AcademicScopeDecision | None) -> ReasonCode | None:
    if decision is None or decision.outcome is AcademicScopeOutcome.SUPPORTED:
        return None
    if decision.outcome is AcademicScopeOutcome.CONTEXT_REQUIRED:
        return ReasonCode.CONTEXT_REQUIRED
    if decision.limitation is AcademicLimitation.PERSONAL_DECISION:
        return ReasonCode.PERSONAL_CASE
    return ReasonCode.MISSING_EVIDENCE


def _authorization_limitation(
    failure: AuthorizationFailureReason | None,
    *,
    context: Context,
) -> AnswerEnvelope:
    reason = (
        {
            AuthorizationFailureReason.CONFLICT: ReasonCode.CONFLICT,
            AuthorizationFailureReason.VERSION_EXPIRED: ReasonCode.EXPIRED_SOURCE,
        }.get(failure, ReasonCode.SOURCE_UNAVAILABLE)
        if failure is not None
        else ReasonCode.SOURCE_UNAVAILABLE
    )
    safe_failure = assemble_safe_failure(
        reason,
        evidence=(),
        scope=RetrievalScope(
            topic="authorization",
            institution=INSTITUTION,
            **context.model_dump(exclude_none=True),
        ),
    )
    return _safe_failure_envelope(
        safe_failure,
        context.model_dump(exclude_none=True),
    )


__all__ = [
    "EvidenceRetriever",
    "RequestMessageProcessor",
    "RetrievalResult",
    "RuntimeEvidenceRetriever",
    "build_runtime_message_processor",
]
