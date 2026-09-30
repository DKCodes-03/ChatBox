from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CorpusBuild, CorpusBuildStatus, SourceChunk, SourceDocument, SourceStatus

DEFAULT_FRESHNESS_WINDOW_DAYS = 365
DEFAULT_ARCHIVE_WINDOW_DAYS = 730


def normalize_query(query: str | None) -> str:
    """Normalize a user question for lexical comparison and embedding input."""
    if query is None:
        return ""
    if not isinstance(query, str):
        query = str(query)
    text = unicodedata.normalize("NFKC", query).lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_scope_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        cleaned = re.sub(r"\s+", " ", value.strip().lower())
        return cleaned or None
    return normalize_scope_value(str(value))


def _read_field(candidate: Any, *names: str) -> Any:
    if candidate is None:
        return None
    if isinstance(candidate, Mapping):
        for name in names:
            if name in candidate:
                return candidate[name]
        return None
    for name in names:
        if hasattr(candidate, name):
            return getattr(candidate, name)
    return None


def _coerce_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(cleaned, fmt).date()
            except ValueError:
                continue
    return None


def _coerce_status(value: Any) -> SourceStatus | None:
    if value is None:
        return None
    if isinstance(value, SourceStatus):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if not normalized:
            return None
        try:
            return SourceStatus(normalized)
        except ValueError:
            aliases = {
                "approved": SourceStatus.ACTIVE,
                "live": SourceStatus.ACTIVE,
                "current": SourceStatus.ACTIVE,
                "reviewed": SourceStatus.ACTIVE,
                "expired": SourceStatus.ARCHIVED,
                "obsolete": SourceStatus.ARCHIVED,
                "needs_review": SourceStatus.DISPUTED,
                "unreviewed": SourceStatus.DISPUTED,
                "conflict": SourceStatus.DISPUTED,
            }
            return aliases.get(normalized)
    return None


def _scope_matches(candidate: Any, requested: Any) -> bool:
    candidate_value = normalize_scope_value(candidate)
    requested_value = normalize_scope_value(requested)
    if requested_value is None:
        return True
    if candidate_value is None:
        return True
    if candidate_value in {"both", "all"}:
        return True
    return candidate_value == requested_value


def _is_fresh(value: Any, *, freshness_window_days: int = DEFAULT_FRESHNESS_WINDOW_DAYS) -> bool:
    last_updated = _coerce_date(value)
    if last_updated is None:
        return False
    age = (date.today() - last_updated).days
    return age <= freshness_window_days


def _candidate_is_active(candidate: Any, *, freshness_window_days: int = DEFAULT_FRESHNESS_WINDOW_DAYS) -> bool:
    status = _coerce_status(_read_field(candidate, "status", "source_status", "review_status"))
    if status is SourceStatus.ARCHIVED or status is SourceStatus.DISPUTED:
        return False
    if status is SourceStatus.ACTIVE:
        return _is_fresh(
            _read_field(candidate, "last_updated", "updated_at", "review_date"),
            freshness_window_days=freshness_window_days,
        ) or _read_field(candidate, "last_updated", "updated_at", "review_date") is not None
    if status is None:
        return False
    return False


def filter_active_retrieval_candidates(
    candidates: Iterable[Any],
    *,
    campus_scope: str | None = None,
    academic_term: str | None = None,
    status: str | SourceStatus | None = None,
    freshness_window_days: int = DEFAULT_FRESHNESS_WINDOW_DAYS,
) -> list[Any]:
    """Return active, fresh candidates filtered by campus, term, and status metadata."""
    matches: list[Any] = []
    for candidate in candidates:
        candidate_status = _coerce_status(
            _read_field(candidate, "status", "source_status", "review_status")
        )
        if status is not None:
            expected_status = _coerce_status(status)
            if candidate_status is not expected_status:
                continue
        elif candidate_status is None or candidate_status is SourceStatus.ARCHIVED or candidate_status is SourceStatus.DISPUTED:
            continue

        if campus_scope is not None and not _scope_matches(
            _read_field(candidate, "campus_scope", "campus"),
            campus_scope,
        ):
            continue

        candidate_term = _read_field(candidate, "academic_term", "term")
        if academic_term is not None:
            if candidate_term is None:
                candidate_term = _read_field(candidate, "term_context")
            if candidate_term is not None and normalize_scope_value(candidate_term) != normalize_scope_value(academic_term):
                continue

        last_updated = _coerce_date(_read_field(candidate, "last_updated", "updated_at", "review_date"))
        if last_updated is not None:
            age_days = (date.today() - last_updated).days
            if age_days > DEFAULT_ARCHIVE_WINDOW_DAYS:
                continue
            if age_days > freshness_window_days:
                continue
        elif candidate_status is SourceStatus.ACTIVE:
            continue
        matches.append(candidate)
    return matches


@dataclass(frozen=True, slots=True)
class RetrievalHit:
    id: Any
    document_id: Any
    document_title: str | None
    document_url: str | None
    heading: str | None
    content: str
    campus_scope: str | None
    academic_term: str | None
    status: str | None
    similarity: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "document_id": self.document_id,
            "document_title": self.document_title,
            "document_url": self.document_url,
            "heading": self.heading,
            "content": self.content,
            "campus_scope": self.campus_scope,
            "academic_term": self.academic_term,
            "status": self.status,
            "similarity": self.similarity,
        }


class RetrievalService:
    """Query normalized text against the active corpus and return ranked evidence."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        embedding_provider: Any | None = None,
        limit: int = 5,
        freshness_window_days: int = DEFAULT_FRESHNESS_WINDOW_DAYS,
    ) -> None:
        self.session = session
        self.embedding_provider = embedding_provider
        self.limit = limit
        self.freshness_window_days = freshness_window_days

    async def get_active_corpus(self) -> CorpusBuild | None:
        result = await self.session.execute(
            select(CorpusBuild)
            .where(CorpusBuild.status == CorpusBuildStatus.PROMOTED)
            .order_by(CorpusBuild.started_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def embed_query(self, question: str) -> list[float]:
        text = normalize_query(question)
        if not text:
            raise ValueError("question text must not be empty")
        if self.embedding_provider is None:
            return [float(len(text.split()))]
        return await self.embedding_provider.embed_one(text)

    async def search(
        self,
        question: str,
        *,
        campus_scope: str | None = None,
        academic_term: str | None = None,
        limit: int | None = None,
    ) -> list[RetrievalHit]:
        normalized = normalize_query(question)
        if not normalized:
            return []

        active_corpus = await self.get_active_corpus()
        if active_corpus is None:
            return []

        query_vector = await self.embed_query(normalized)
        limit_value = self.limit if limit is None else limit
        if limit_value <= 0:
            return []

        chunk_filter = [
            SourceChunk.corpus_version == active_corpus.version,
            SourceDocument.status == SourceStatus.ACTIVE,
        ]
        if campus_scope is not None:
            chunk_filter.append(SourceDocument.campus_scope.in_({campus_scope, "both", "all", None}))
        if academic_term is not None:
            chunk_filter.append(
                or_(
                    SourceDocument.academic_term.is_(None),
                    SourceDocument.academic_term == academic_term,
                )
            )
        if active_corpus.version:
            chunk_filter.append(SourceDocument.corpus_version == active_corpus.version)

        stmt = (
            select(
                SourceChunk,
                SourceDocument,
                func.cosine_distance(SourceChunk.embedding, query_vector).label("similarity"),
            )
            .join(SourceDocument, SourceChunk.document_id == SourceDocument.id)
            .where(and_(*chunk_filter))
            .order_by(func.cosine_distance(SourceChunk.embedding, query_vector))
            .limit(limit_value)
        )

        try:
            rows = (await self.session.execute(stmt)).all()
        except Exception:
            rows = []

        results: list[RetrievalHit] = []
        for row in rows:
            chunk = row[0]
            document = row[1]
            similarity = float(row[2]) if row[2] is not None else None
            if document.status is not SourceStatus.ACTIVE:
                continue
            if campus_scope and not _scope_matches(document.campus_scope, campus_scope):
                continue
            if academic_term and document.academic_term and normalize_scope_value(document.academic_term) != normalize_scope_value(academic_term):
                continue
            if document.last_updated is not None and (date.today() - document.last_updated).days > self.freshness_window_days:
                continue
            results.append(
                RetrievalHit(
                    id=chunk.id,
                    document_id=document.id,
                    document_title=document.title,
                    document_url=document.url,
                    heading=chunk.heading,
                    content=chunk.content,
                    campus_scope=document.campus_scope,
                    academic_term=document.academic_term,
                    status=document.status.value if isinstance(document.status, SourceStatus) else str(document.status),
                    similarity=similarity,
                )
            )

        if results:
            return results

        # Fall back to lexical evidence when embeddings are unavailable or the DB search fails.
        fallback_stmt = (
            select(SourceChunk, SourceDocument)
            .join(SourceDocument, SourceChunk.document_id == SourceDocument.id)
            .where(
                and_(
                    SourceChunk.corpus_version == active_corpus.version,
                    SourceDocument.status == SourceStatus.ACTIVE,
                    SourceDocument.corpus_version == active_corpus.version,
                )
            )
            .limit(limit_value)
        )
        fallback_rows = (await self.session.execute(fallback_stmt)).all()
        lexical_candidates = []
        for row in fallback_rows:
            chunk = row[0]
            document = row[1]
            chunk_text = f"{chunk.heading or ''} {chunk.content}".lower()
            if not normalized or normalized in chunk_text:
                lexical_candidates.append((chunk, document, 1.0))
            elif set(normalized.split()) & set(chunk_text.split()):
                lexical_candidates.append((chunk, document, 0.8))
        for chunk, document, similarity in lexical_candidates[:limit_value]:
            if campus_scope and not _scope_matches(document.campus_scope, campus_scope):
                continue
            if academic_term and document.academic_term and normalize_scope_value(document.academic_term) != normalize_scope_value(academic_term):
                continue
            if document.last_updated is not None and (date.today() - document.last_updated).days > self.freshness_window_days:
                continue
            results.append(
                RetrievalHit(
                    id=chunk.id,
                    document_id=document.id,
                    document_title=document.title,
                    document_url=document.url,
                    heading=chunk.heading,
                    content=chunk.content,
                    campus_scope=document.campus_scope,
                    academic_term=document.academic_term,
                    status=document.status.value if isinstance(document.status, SourceStatus) else str(document.status),
                    similarity=similarity,
                )
            )
        return results


async def search_relevant_chunks(
    session: AsyncSession,
    question: str,
    *,
    campus_scope: str | None = None,
    academic_term: str | None = None,
    embedding_provider: Any | None = None,
    limit: int = 5,
) -> list[RetrievalHit]:
    return await RetrievalService(
        session,
        embedding_provider=embedding_provider,
        limit=limit,
    ).search(question, campus_scope=campus_scope, academic_term=academic_term, limit=limit)


async def retrieve_relevant_chunks(
    session: AsyncSession,
    question: str,
    *,
    campus_scope: str | None = None,
    academic_term: str | None = None,
    embedding_provider: Any | None = None,
    limit: int = 5,
) -> list[RetrievalHit]:
    return await search_relevant_chunks(
        session,
        question,
        campus_scope=campus_scope,
        academic_term=academic_term,
        embedding_provider=embedding_provider,
        limit=limit,
    )


__all__ = [
    "DEFAULT_ARCHIVE_WINDOW_DAYS",
    "DEFAULT_FRESHNESS_WINDOW_DAYS",
    "RetrievalHit",
    "RetrievalService",
    "filter_active_retrieval_candidates",
    "normalize_query",
    "normalize_scope_value",
    "retrieve_relevant_chunks",
    "search_relevant_chunks",
]
