"""Exact vector and full-text retrieval over currently eligible evidence."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Self, cast
from uuid import UUID

import numpy as np
from app.config import Settings
from app.models.enums import (
    ConflictStatus,
    ExtractionStatus,
    QualificationStatus,
    SourceStatus,
)
from app.models.guidance import Conflict, CourseRelation, conflict_evidence_blocks
from app.models.sources import (
    Applicability,
    Embedding,
    EvidenceBlock,
    Qualification,
    Source,
    SourceVersion,
)
from app.retrieval.embeddings import (
    EMBEDDING_DIMENSIONS,
    MAX_QUERY_CHARACTERS,
    QueryVectorClearedError,
    TemporaryQueryVector,
)
from numpy.typing import NDArray
from sqlalchemy import String, any_, exists, false, func, literal, or_, select, union_all
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, aliased
from sqlalchemy.sql import Select

MAX_CANDIDATES_PER_CHANNEL = 20
MAX_SELECTED_EVIDENCE = 8
MAX_SCOPE_VALUE_CHARACTERS = 255
FULL_TEXT_CONFIGURATION = "english"
RANK_FUSION_CONSTANT = 60
MAX_STRUCTURED_COURSE_CODES = 8
_COURSE_CODE_RE = re.compile(r"\b([A-Za-z]{2,8})\s*-?\s*(\d{5}[A-Za-z]?)\b")

JsonContainer = dict[str, object] | list[object]
FloatVector = NDArray[np.float32]


class RetrievalError(RuntimeError):
    """Base class for errors that do not expose query or evidence content."""


class InvalidRetrievalInputError(RetrievalError):
    def __init__(self) -> None:
        super().__init__("retrieval input is invalid")


class RetrievalUnavailableError(RetrievalError):
    def __init__(self) -> None:
        super().__init__("evidence retrieval is unavailable")


class RetrievalChannel(StrEnum):
    VECTOR = "vector"
    FULL_TEXT = "full_text"
    STRUCTURED = "structured"


@dataclass(frozen=True, slots=True)
class RetrievalScope:
    """Explicit context used to reject inapplicable or overly specific evidence."""

    topic: str
    institution: str
    campus: str | None = None
    student_level: str | None = None
    program: str | None = None
    catalog_year: str | None = None
    term: str | None = None
    session: str | None = None
    year: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "topic", self._required(self.topic, maximum=128))
        object.__setattr__(self, "institution", self._required(self.institution))
        for name in (
            "campus",
            "student_level",
            "program",
            "catalog_year",
            "term",
            "session",
            "year",
        ):
            value = cast(str | None, getattr(self, name))
            object.__setattr__(self, name, self._optional(value))

    @staticmethod
    def _required(value: str, *, maximum: int = MAX_SCOPE_VALUE_CHARACTERS) -> str:
        if not isinstance(value, str):
            raise InvalidRetrievalInputError()
        cleaned = value.strip()
        if not cleaned or len(cleaned) > maximum:
            raise InvalidRetrievalInputError()
        return cleaned

    @classmethod
    def _optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return cls._required(value)


@dataclass(frozen=True, slots=True)
class EvidenceApplicability:
    topic: str
    institution: str
    campus: str | None
    student_level: str | None
    program: str | None
    catalog_year: str | None
    term: str | None
    session: str | None
    year: str | None = None


@dataclass(frozen=True, slots=True)
class RetrievedEvidence:
    """One complete eligible evidence unit in a channel-specific ranking."""

    evidence_id: UUID
    version_id: UUID
    source_id: UUID
    source_title: str
    canonical_url: str
    ordinal: int
    heading_path: tuple[str, ...]
    page: int | None
    anchor: str | None
    text: str = field(repr=False)
    structured_content: JsonContainer = field(repr=False)
    topic_key: str
    scope: dict[str, object] = field(repr=False)
    applicability: EvidenceApplicability
    model_revision: str
    channel: RetrievalChannel
    rank: int
    ranking_value: float


@dataclass(frozen=True, slots=True)
class RetrievalCandidates:
    """Channel rankings that can produce one bounded, deduplicated evidence set."""

    vector: tuple[RetrievedEvidence, ...]
    full_text: tuple[RetrievedEvidence, ...]
    structured: tuple[RetrievedEvidence, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.vector and not self.full_text and not self.structured

    def select(
        self,
        *,
        maximum_evidence: int = MAX_SELECTED_EVIDENCE,
    ) -> tuple[RetrievedEvidence, ...]:
        """Fuse channel ranks with deterministic reciprocal-rank fusion."""

        if (
            isinstance(maximum_evidence, bool)
            or not isinstance(maximum_evidence, int)
            or maximum_evidence < 1
            or maximum_evidence > MAX_SELECTED_EVIDENCE
        ):
            raise ValueError("maximum evidence must be between one and eight")
        scores: defaultdict[UUID, float] = defaultdict(float)
        evidence_by_id: dict[UUID, RetrievedEvidence] = {}
        seen_channel_evidence: set[tuple[RetrievalChannel, UUID]] = set()
        for expected_channel, ranking in (
            (RetrievalChannel.VECTOR, self.vector),
            (RetrievalChannel.FULL_TEXT, self.full_text),
            (RetrievalChannel.STRUCTURED, self.structured),
        ):
            for item in ranking:
                if (
                    not isinstance(item, RetrievedEvidence)
                    or item.channel is not expected_channel
                    or item.rank < 1
                ):
                    raise RetrievalUnavailableError()
                channel_key = (expected_channel, item.evidence_id)
                if channel_key in seen_channel_evidence:
                    continue
                seen_channel_evidence.add(channel_key)
                scores[item.evidence_id] += 1.0 / (RANK_FUSION_CONSTANT + item.rank)
                evidence_by_id.setdefault(item.evidence_id, item)
        ordered_ids = sorted(scores, key=lambda item: (-scores[item], str(item)))
        return tuple(evidence_by_id[evidence_id] for evidence_id in ordered_ids[:maximum_evidence])


class EvidenceSearch:
    """Run all retrieval rankings in one PostgreSQL statement and database snapshot."""

    def __init__(
        self,
        session: Session,
        *,
        model_revision: str,
        candidate_limit: int = MAX_CANDIDATES_PER_CHANNEL,
    ) -> None:
        cleaned_revision = model_revision.strip() if isinstance(model_revision, str) else ""
        if not cleaned_revision or len(cleaned_revision) > 255:
            raise ValueError("embedding model revision is invalid")
        if candidate_limit < 1 or candidate_limit > MAX_CANDIDATES_PER_CHANNEL:
            raise ValueError("candidate limit must be between one and twenty")
        self._session = session
        self._model_revision = cleaned_revision
        self._candidate_limit = candidate_limit

    @classmethod
    def from_settings(cls, session: Session, settings: Settings) -> Self:
        return cls(
            session,
            model_revision=settings.embedding_model,
            candidate_limit=MAX_CANDIDATES_PER_CHANNEL,
        )

    def search(
        self,
        *,
        query: str,
        query_vector: TemporaryQueryVector,
        scope: RetrievalScope,
        observed_at: datetime | None = None,
        include_unresolved_conflicts: bool = False,
    ) -> RetrievalCandidates:
        """Return qualified candidates without persisting query text or vectors.

        Unresolved conflicts remain excluded by default. The diagnostic option exists only so a
        safe-failure response can cite the disputed sources without using them for a claim.
        """

        cleaned_query = self._validated_query(query)
        checked_at = self._validated_time(observed_at)
        if not isinstance(scope, RetrievalScope):
            raise InvalidRetrievalInputError()
        if not isinstance(include_unresolved_conflicts, bool):
            raise InvalidRetrievalInputError()
        vector_values = self._validated_vector(query_vector)
        statement = self._build_statement(
            query=cleaned_query,
            query_vector=vector_values,
            scope=scope,
            observed_at=checked_at,
            include_unresolved_conflicts=include_unresolved_conflicts,
        )

        try:
            rows = self._session.execute(statement).mappings().all()
        except SQLAlchemyError:
            raise RetrievalUnavailableError() from None

        vector: list[RetrievedEvidence] = []
        full_text: list[RetrievedEvidence] = []
        structured: list[RetrievedEvidence] = []
        for row in rows:
            channel = RetrievalChannel(cast(str, row["channel"]))
            target = {
                RetrievalChannel.VECTOR: vector,
                RetrievalChannel.FULL_TEXT: full_text,
                RetrievalChannel.STRUCTURED: structured,
            }[channel]
            target.append(
                self._candidate(
                    row,
                    channel=channel,
                    rank=cast(int, row["channel_rank"]),
                )
            )
        return RetrievalCandidates(
            vector=tuple(vector),
            full_text=tuple(full_text),
            structured=tuple(structured),
        )

    def _build_statement(
        self,
        *,
        query: str,
        query_vector: FloatVector,
        scope: RetrievalScope,
        observed_at: datetime,
        include_unresolved_conflicts: bool,
    ) -> Select[tuple[object, ...]]:
        latest_pass = aliased(Qualification, name="latest_pass")
        later_check = aliased(Qualification, name="later_check")
        same_time_failure = aliased(Qualification, name="same_time_failure")
        newer_version = aliased(SourceVersion, name="newer_version")
        newer_pass = aliased(Qualification, name="newer_pass")
        newer_later_check = aliased(Qualification, name="newer_later_check")
        newer_same_time_failure = aliased(Qualification, name="newer_same_time_failure")

        has_current_qualification = exists(
            select(1).where(
                latest_pass.version_id == SourceVersion.id,
                latest_pass.status == QualificationStatus.PASSED,
                latest_pass.valid_until > observed_at,
                ~exists(
                    select(1).where(
                        later_check.version_id == latest_pass.version_id,
                        later_check.checked_at > latest_pass.checked_at,
                    )
                ),
                ~exists(
                    select(1).where(
                        same_time_failure.version_id == latest_pass.version_id,
                        same_time_failure.checked_at == latest_pass.checked_at,
                        same_time_failure.status != QualificationStatus.PASSED,
                    )
                ),
            )
        )
        has_unresolved_conflict = exists(
            select(1)
            .select_from(
                conflict_evidence_blocks.join(
                    Conflict,
                    Conflict.id == conflict_evidence_blocks.c.conflict_id,
                )
            )
            .where(
                conflict_evidence_blocks.c.evidence_block_id == EvidenceBlock.id,
                Conflict.status == ConflictStatus.UNRESOLVED,
            )
        )
        has_newer_current_version = exists(
            select(1)
            .select_from(newer_version)
            .join(newer_pass, newer_pass.version_id == newer_version.id)
            .where(
                newer_version.source_id == Source.id,
                newer_version.fetched_at > SourceVersion.fetched_at,
                newer_version.extraction_status == ExtractionStatus.COMPLETE,
                or_(
                    newer_version.effective_from.is_(None),
                    newer_version.effective_from <= observed_at,
                ),
                or_(
                    newer_version.effective_to.is_(None),
                    newer_version.effective_to > observed_at,
                ),
                newer_pass.status == QualificationStatus.PASSED,
                newer_pass.valid_until > observed_at,
                ~exists(
                    select(1).where(
                        newer_later_check.version_id == newer_pass.version_id,
                        newer_later_check.checked_at > newer_pass.checked_at,
                    )
                ),
                ~exists(
                    select(1).where(
                        newer_same_time_failure.version_id == newer_pass.version_id,
                        newer_same_time_failure.checked_at == newer_pass.checked_at,
                        newer_same_time_failure.status != QualificationStatus.PASSED,
                    )
                ),
            )
        )

        applicability_filters = [
            Applicability.topic == scope.topic,
            Applicability.institution == scope.institution,
            EvidenceBlock.id == any_(Applicability.evidence_block_ids),
        ]
        evidence_year = func.nullif(
            func.btrim(EvidenceBlock.scope["year"].as_string()),
            "",
        )
        for column, value in (
            (Applicability.campus, scope.campus),
            (Applicability.student_level, scope.student_level),
            (Applicability.program, scope.program),
            (Applicability.catalog_year, scope.catalog_year),
            (Applicability.term, scope.term),
            (Applicability.session, scope.session),
            (evidence_year, scope.year),
        ):
            if value is None:
                applicability_filters.append(column.is_(None))
            else:
                applicability_filters.append(column == value)

        eligibility_filters = [
            Source.status == SourceStatus.ELIGIBLE,
            SourceVersion.extraction_status == ExtractionStatus.COMPLETE,
            or_(
                SourceVersion.effective_from.is_(None),
                SourceVersion.effective_from <= observed_at,
            ),
            or_(
                SourceVersion.effective_to.is_(None),
                SourceVersion.effective_to > observed_at,
            ),
            EvidenceBlock.topic_key == scope.topic,
            Embedding.model_revision == self._model_revision,
            Embedding.dimensions == EMBEDDING_DIMENSIONS,
            has_current_qualification,
            ~has_newer_current_version,
            *applicability_filters,
        ]
        if not include_unresolved_conflicts:
            eligibility_filters.append(~has_unresolved_conflict)

        eligible = (
            select(
                EvidenceBlock.id.label("evidence_id"),
                EvidenceBlock.version_id,
                SourceVersion.source_id,
                Source.title.label("source_title"),
                Source.canonical_url,
                EvidenceBlock.ordinal,
                EvidenceBlock.heading_path,
                EvidenceBlock.page,
                EvidenceBlock.anchor,
                EvidenceBlock.text,
                EvidenceBlock.structured_content,
                EvidenceBlock.topic_key,
                EvidenceBlock.scope.label("evidence_scope"),
                Applicability.topic.label("applicability_topic"),
                Applicability.institution.label("applicability_institution"),
                Applicability.campus.label("applicability_campus"),
                Applicability.student_level.label("applicability_student_level"),
                Applicability.program.label("applicability_program"),
                Applicability.catalog_year.label("applicability_catalog_year"),
                Applicability.term.label("applicability_term"),
                Applicability.session.label("applicability_session"),
                evidence_year.label("applicability_year"),
                Embedding.model_revision,
                Embedding.vector,
            )
            .join(SourceVersion, SourceVersion.id == EvidenceBlock.version_id)
            .join(Source, Source.id == SourceVersion.source_id)
            .join(Embedding, Embedding.block_id == EvidenceBlock.id)
            .join(Applicability, Applicability.version_id == SourceVersion.id)
            .where(*eligibility_filters)
            .cte("eligible_evidence")
        )

        result_columns = (
            eligible.c.evidence_id,
            eligible.c.version_id,
            eligible.c.source_id,
            eligible.c.source_title,
            eligible.c.canonical_url,
            eligible.c.ordinal,
            eligible.c.heading_path,
            eligible.c.page,
            eligible.c.anchor,
            eligible.c.text,
            eligible.c.structured_content,
            eligible.c.topic_key,
            eligible.c.evidence_scope,
            eligible.c.applicability_topic,
            eligible.c.applicability_institution,
            eligible.c.applicability_campus,
            eligible.c.applicability_student_level,
            eligible.c.applicability_program,
            eligible.c.applicability_catalog_year,
            eligible.c.applicability_term,
            eligible.c.applicability_session,
            eligible.c.applicability_year,
            eligible.c.model_revision,
        )
        vector_distance = eligible.c.vector.cosine_distance(query_vector)
        vector_candidates = (
            select(
                *result_columns,
                literal(RetrievalChannel.VECTOR.value, type_=String()).label("channel"),
                func.row_number()
                .over(order_by=(vector_distance.asc(), eligible.c.evidence_id.asc()))
                .label("channel_rank"),
                vector_distance.label("ranking_value"),
            )
            .order_by(vector_distance.asc(), eligible.c.evidence_id.asc())
            .limit(self._candidate_limit)
        )

        search_document = func.to_tsvector(
            FULL_TEXT_CONFIGURATION,
            func.concat_ws(
                " ",
                func.array_to_string(eligible.c.heading_path, " "),
                eligible.c.text,
            ),
        )
        search_query = func.websearch_to_tsquery(FULL_TEXT_CONFIGURATION, query)
        text_rank = func.ts_rank_cd(search_document, search_query)
        full_text_candidates = (
            select(
                *result_columns,
                literal(RetrievalChannel.FULL_TEXT.value, type_=String()).label("channel"),
                func.row_number()
                .over(order_by=(text_rank.desc(), eligible.c.evidence_id.asc()))
                .label("channel_rank"),
                text_rank.label("ranking_value"),
            )
            .where(search_document.op("@@")(search_query))
            .order_by(text_rank.desc(), eligible.c.evidence_id.asc())
            .limit(self._candidate_limit)
        )

        course_codes = _structured_course_codes(query)
        structured_match = (
            exists(
                select(1).where(
                    CourseRelation.evidence_block_id == eligible.c.evidence_id,
                    func.upper(CourseRelation.course_code).in_(course_codes),
                )
            )
            if course_codes
            else false()
        )
        structured_candidates = (
            select(
                *result_columns,
                literal(RetrievalChannel.STRUCTURED.value, type_=String()).label("channel"),
                func.row_number().over(order_by=eligible.c.evidence_id.asc()).label("channel_rank"),
                literal(1.0).label("ranking_value"),
            )
            .where(structured_match)
            .order_by(eligible.c.evidence_id.asc())
            .limit(self._candidate_limit)
        )

        combined = union_all(
            vector_candidates,
            full_text_candidates,
            structured_candidates,
        ).subquery("ranked_candidates")
        return select(combined).order_by(
            combined.c.channel.asc(),
            combined.c.channel_rank.asc(),
        )

    @staticmethod
    def _candidate(
        row: RowMapping,
        *,
        channel: RetrievalChannel,
        rank: int,
    ) -> RetrievedEvidence:
        structured_content = cast(JsonContainer, deepcopy(row["structured_content"]))
        evidence_scope = cast(dict[str, object], deepcopy(row["evidence_scope"]))
        ranking_value = float(row["ranking_value"])
        if rank < 1 or not math.isfinite(ranking_value):
            raise RetrievalUnavailableError()
        return RetrievedEvidence(
            evidence_id=cast(UUID, row["evidence_id"]),
            version_id=cast(UUID, row["version_id"]),
            source_id=cast(UUID, row["source_id"]),
            source_title=cast(str, row["source_title"]),
            canonical_url=cast(str, row["canonical_url"]),
            ordinal=cast(int, row["ordinal"]),
            heading_path=tuple(cast(list[str], row["heading_path"])),
            page=cast(int | None, row["page"]),
            anchor=cast(str | None, row["anchor"]),
            text=cast(str, row["text"]),
            structured_content=structured_content,
            topic_key=cast(str, row["topic_key"]),
            scope=evidence_scope,
            applicability=EvidenceApplicability(
                topic=cast(str, row["applicability_topic"]),
                institution=cast(str, row["applicability_institution"]),
                campus=cast(str | None, row["applicability_campus"]),
                student_level=cast(str | None, row["applicability_student_level"]),
                program=cast(str | None, row["applicability_program"]),
                catalog_year=cast(str | None, row["applicability_catalog_year"]),
                term=cast(str | None, row["applicability_term"]),
                session=cast(str | None, row["applicability_session"]),
                year=cast(str | None, row["applicability_year"]),
            ),
            model_revision=cast(str, row["model_revision"]),
            channel=channel,
            rank=rank,
            ranking_value=ranking_value,
        )

    @staticmethod
    def _validated_query(query: str) -> str:
        if not isinstance(query, str):
            raise InvalidRetrievalInputError()
        cleaned = query.strip()
        if not cleaned or len(cleaned) > MAX_QUERY_CHARACTERS:
            raise InvalidRetrievalInputError()
        return cleaned

    @staticmethod
    def _validated_vector(
        query_vector: TemporaryQueryVector,
    ) -> FloatVector:
        if not isinstance(query_vector, TemporaryQueryVector):
            raise InvalidRetrievalInputError()
        try:
            values = query_vector.values
        except QueryVectorClearedError:
            raise InvalidRetrievalInputError() from None
        if values.shape != (EMBEDDING_DIMENSIONS,) or not np.isfinite(values).all():
            raise InvalidRetrievalInputError()
        norm = float(np.linalg.norm(values))
        if not math.isfinite(norm) or not math.isclose(norm, 1.0, rel_tol=1e-5, abs_tol=1e-5):
            raise InvalidRetrievalInputError()
        return values

    @staticmethod
    def _validated_time(observed_at: datetime | None) -> datetime:
        value = datetime.now(UTC) if observed_at is None else observed_at
        if value.tzinfo is None or value.utcoffset() is None:
            raise InvalidRetrievalInputError()
        return value.astimezone(UTC)


def _structured_course_codes(query: str) -> tuple[str, ...]:
    codes = dict.fromkeys(
        f"{subject.upper()} {number.upper()}" for subject, number in _COURSE_CODE_RE.findall(query)
    )
    return tuple(codes)[:MAX_STRUCTURED_COURSE_CODES]
