"""Transactional loading of semantic blocks and normalized MiniLM vectors."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.ingestion.chunking import ChunkingResult
from app.models.enums import ExtractionStatus
from app.models.sources import Applicability, Embedding, EvidenceBlock, SourceVersion
from app.retrieval.embeddings import (
    EMBEDDING_DIMENSIONS,
    MODEL_NAME,
    EmbeddingError,
    EmbeddingVector,
    MiniLMEmbeddingService,
)

_NORMALIZATION_TOLERANCE = 1e-5


class IndexingFailureReason(StrEnum):
    """Bounded reasons why a corpus index batch cannot be loaded."""

    EMBEDDING_FAILED = "embedding_failed"
    EXISTING_INDEX_CONFLICT = "existing_index_conflict"
    INVALID_BATCH = "invalid_batch"
    PERSISTENCE_FAILED = "persistence_failed"
    VECTOR_INVALID = "vector_invalid"


class IndexingError(RuntimeError):
    """Base error that never includes evidence text or database details."""

    def __init__(self, reason: IndexingFailureReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


class IndexingInputError(IndexingError):
    """The source version and chunk batch are inconsistent."""


class IndexingEmbeddingError(IndexingError):
    """The approved local embedding service could not produce valid vectors."""


class IndexingConflictError(IndexingError):
    """Persisted rows do not represent one complete, reusable index batch."""


class IndexingPersistenceError(IndexingError):
    """The index batch could not be flushed to the caller-owned transaction."""


@dataclass(frozen=True, slots=True)
class IndexWriteResult:
    """Rows staged in, or reused from, the caller-owned database transaction."""

    blocks: tuple[EvidenceBlock, ...]
    applicability: Applicability
    embeddings: tuple[Embedding, ...]
    created_blocks: bool
    created_embeddings: bool
    model_revision: str
    dimensions: int


class EvidenceIndexer:
    """Embed and stage public corpus evidence without committing independently.

    The caller owns the transaction that also contains versioning and qualification. Embedding
    completes and is validated before new ORM rows are added. One flush then checks all database
    constraints together; any raised error requires the caller to roll back the transaction.
    """

    def __init__(
        self,
        session: Session,
        *,
        embedding_service: MiniLMEmbeddingService,
    ) -> None:
        if not isinstance(session, Session):
            raise TypeError("session must be a SQLAlchemy Session")
        if not isinstance(embedding_service, MiniLMEmbeddingService):
            raise TypeError("embedding_service must be MiniLMEmbeddingService")
        if (
            embedding_service.model_name != MODEL_NAME
            or embedding_service.dimensions != EMBEDDING_DIMENSIONS
        ):
            raise IndexingInputError(IndexingFailureReason.INVALID_BATCH)
        self._session = session
        self._embedding_service = embedding_service

    def index(
        self,
        *,
        version: SourceVersion,
        chunks: ChunkingResult,
    ) -> IndexWriteResult:
        """Reuse a complete index or stage an all-or-nothing block/vector batch."""

        _validate_batch(version, chunks)
        existing_blocks = self._load_existing_blocks(version.id)
        if existing_blocks:
            return self._reuse_or_reindex(
                version=version,
                chunks=chunks,
                existing_blocks=existing_blocks,
            )

        vectors = self._embed(chunks.blocks, chunks.wordpiece_counts)
        embeddings = _embedding_rows(
            chunks.blocks,
            vectors,
            model_revision=self._embedding_service.model_name,
            dimensions=self._embedding_service.dimensions,
        )
        self._stage_and_flush((*chunks.blocks, chunks.applicability, *embeddings))
        return IndexWriteResult(
            blocks=chunks.blocks,
            applicability=chunks.applicability,
            embeddings=embeddings,
            created_blocks=True,
            created_embeddings=True,
            model_revision=self._embedding_service.model_name,
            dimensions=self._embedding_service.dimensions,
        )

    def _reuse_or_reindex(
        self,
        *,
        version: SourceVersion,
        chunks: ChunkingResult,
        existing_blocks: tuple[EvidenceBlock, ...],
    ) -> IndexWriteResult:
        if not _same_block_representation(existing_blocks, chunks.blocks):
            raise IndexingConflictError(IndexingFailureReason.EXISTING_INDEX_CONFLICT)
        existing_applicability = self._load_existing_applicability(
            version.id,
            chunks.applicability.topic,
        )
        if existing_applicability is None or not _same_applicability(
            existing_applicability,
            chunks.applicability,
            evidence_ids=tuple(block.id for block in existing_blocks),
        ):
            raise IndexingConflictError(IndexingFailureReason.EXISTING_INDEX_CONFLICT)

        existing_embeddings = self._load_existing_embeddings(version.id)
        if existing_embeddings:
            if len(existing_embeddings) != len(existing_blocks):
                raise IndexingConflictError(IndexingFailureReason.EXISTING_INDEX_CONFLICT)
            _validate_embedding_rows(
                existing_blocks,
                existing_embeddings,
                model_revision=self._embedding_service.model_name,
                dimensions=self._embedding_service.dimensions,
            )
            return IndexWriteResult(
                blocks=existing_blocks,
                applicability=existing_applicability,
                embeddings=existing_embeddings,
                created_blocks=False,
                created_embeddings=False,
                model_revision=self._embedding_service.model_name,
                dimensions=self._embedding_service.dimensions,
            )

        counts = tuple(_recorded_wordpiece_count(block) for block in existing_blocks)
        vectors = self._embed(existing_blocks, counts)
        embeddings = _embedding_rows(
            existing_blocks,
            vectors,
            model_revision=self._embedding_service.model_name,
            dimensions=self._embedding_service.dimensions,
        )
        self._stage_and_flush(embeddings)
        return IndexWriteResult(
            blocks=existing_blocks,
            applicability=existing_applicability,
            embeddings=embeddings,
            created_blocks=False,
            created_embeddings=True,
            model_revision=self._embedding_service.model_name,
            dimensions=self._embedding_service.dimensions,
        )

    def _embed(
        self,
        blocks: Sequence[EvidenceBlock],
        expected_counts: Sequence[int],
    ) -> tuple[EmbeddingVector, ...]:
        try:
            units = tuple(
                self._embedding_service.prepare_evidence_unit(
                    text=block.text,
                    heading_path=block.heading_path,
                )
                for block in blocks
            )
            if tuple(unit.wordpiece_count for unit in units) != tuple(expected_counts):
                raise IndexingInputError(IndexingFailureReason.INVALID_BATCH)
            vectors = self._embedding_service.embed_evidence_units(units)
        except IndexingError:
            raise
        except EmbeddingError:
            raise IndexingEmbeddingError(IndexingFailureReason.EMBEDDING_FAILED) from None
        if len(vectors) != len(blocks):
            raise IndexingEmbeddingError(IndexingFailureReason.VECTOR_INVALID)
        for vector in vectors:
            _validate_vector(vector.values, dimensions=self._embedding_service.dimensions)
        return vectors

    def _load_existing_blocks(self, version_id: UUID) -> tuple[EvidenceBlock, ...]:
        try:
            return tuple(
                self._session.scalars(
                    select(EvidenceBlock)
                    .where(EvidenceBlock.version_id == version_id)
                    .order_by(EvidenceBlock.ordinal)
                ).all()
            )
        except SQLAlchemyError:
            raise IndexingPersistenceError(IndexingFailureReason.PERSISTENCE_FAILED) from None

    def _load_existing_applicability(
        self,
        version_id: UUID,
        topic: str,
    ) -> Applicability | None:
        try:
            return self._session.scalar(
                select(Applicability).where(
                    Applicability.version_id == version_id,
                    Applicability.topic == topic,
                )
            )
        except SQLAlchemyError:
            raise IndexingPersistenceError(IndexingFailureReason.PERSISTENCE_FAILED) from None

    def _load_existing_embeddings(self, version_id: UUID) -> tuple[Embedding, ...]:
        try:
            return tuple(
                self._session.scalars(
                    select(Embedding)
                    .join(EvidenceBlock, EvidenceBlock.id == Embedding.block_id)
                    .where(
                        EvidenceBlock.version_id == version_id,
                        Embedding.model_revision == self._embedding_service.model_name,
                    )
                    .order_by(EvidenceBlock.ordinal)
                ).all()
            )
        except SQLAlchemyError:
            raise IndexingPersistenceError(IndexingFailureReason.PERSISTENCE_FAILED) from None

    def _stage_and_flush(self, rows: Sequence[object]) -> None:
        try:
            self._session.add_all(rows)
            self._session.flush()
        except SQLAlchemyError:
            raise IndexingPersistenceError(IndexingFailureReason.PERSISTENCE_FAILED) from None


def _validate_batch(version: SourceVersion, chunks: ChunkingResult) -> None:
    if (
        not isinstance(version, SourceVersion)
        or not isinstance(version.id, UUID)
        or version.extraction_status is not ExtractionStatus.COMPLETE
        or not isinstance(chunks, ChunkingResult)
        or not chunks.blocks
    ):
        raise IndexingInputError(IndexingFailureReason.INVALID_BATCH)
    blocks = chunks.blocks
    if (
        len(blocks) != len(chunks.wordpiece_counts)
        or len({block.id for block in blocks}) != len(blocks)
        or [block.ordinal for block in blocks] != list(range(len(blocks)))
        or any(
            not isinstance(block.id, UUID)
            or block.version_id != version.id
            or not block.text.strip()
            or not block.topic_key.strip()
            for block in blocks
        )
    ):
        raise IndexingInputError(IndexingFailureReason.INVALID_BATCH)
    applicability = chunks.applicability
    if (
        applicability.version_id != version.id
        or applicability.topic != blocks[0].topic_key
        or any(block.topic_key != applicability.topic for block in blocks)
        or applicability.evidence_block_ids != [block.id for block in blocks]
    ):
        raise IndexingInputError(IndexingFailureReason.INVALID_BATCH)
    for block, expected_count in zip(blocks, chunks.wordpiece_counts, strict=True):
        if (
            isinstance(expected_count, bool)
            or not isinstance(expected_count, int)
            or expected_count < 1
            or _recorded_wordpiece_count(block) != expected_count
        ):
            raise IndexingInputError(IndexingFailureReason.INVALID_BATCH)


def _recorded_wordpiece_count(block: EvidenceBlock) -> int:
    structured = block.structured_content
    if not isinstance(structured, dict):
        raise IndexingInputError(IndexingFailureReason.INVALID_BATCH)
    count = structured.get("embedding_wordpiece_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise IndexingInputError(IndexingFailureReason.INVALID_BATCH)
    return count


def _embedding_rows(
    blocks: Sequence[EvidenceBlock],
    vectors: Sequence[EmbeddingVector],
    *,
    model_revision: str,
    dimensions: int,
) -> tuple[Embedding, ...]:
    return tuple(
        Embedding(
            block_id=block.id,
            model_revision=model_revision,
            dimensions=dimensions,
            vector=list(vector.values),
        )
        for block, vector in zip(blocks, vectors, strict=True)
    )


def _validate_embedding_rows(
    blocks: Sequence[EvidenceBlock],
    embeddings: Sequence[Embedding],
    *,
    model_revision: str,
    dimensions: int,
) -> None:
    for block, embedding in zip(blocks, embeddings, strict=True):
        if (
            embedding.block_id != block.id
            or embedding.model_revision != model_revision
            or embedding.dimensions != dimensions
        ):
            raise IndexingConflictError(IndexingFailureReason.EXISTING_INDEX_CONFLICT)
        try:
            _validate_vector(
                tuple(float(value) for value in embedding.vector), dimensions=dimensions
            )
        except IndexingEmbeddingError:
            raise IndexingConflictError(IndexingFailureReason.EXISTING_INDEX_CONFLICT) from None


def _validate_vector(values: Sequence[float], *, dimensions: int) -> None:
    if len(values) != dimensions or not all(math.isfinite(value) for value in values):
        raise IndexingEmbeddingError(IndexingFailureReason.VECTOR_INVALID)
    norm = math.sqrt(math.fsum(value * value for value in values))
    if not math.isfinite(norm) or abs(norm - 1.0) > _NORMALIZATION_TOLERANCE:
        raise IndexingEmbeddingError(IndexingFailureReason.VECTOR_INVALID)


def _same_block_representation(
    existing: Sequence[EvidenceBlock],
    proposed: Sequence[EvidenceBlock],
) -> bool:
    if len(existing) != len(proposed):
        return False
    return all(
        (
            left.ordinal,
            left.heading_path,
            left.page,
            left.anchor,
            left.text,
            left.structured_content,
            left.topic_key,
            left.scope,
        )
        == (
            right.ordinal,
            right.heading_path,
            right.page,
            right.anchor,
            right.text,
            right.structured_content,
            right.topic_key,
            right.scope,
        )
        for left, right in zip(existing, proposed, strict=True)
    )


def _same_applicability(
    existing: Applicability,
    proposed: Applicability,
    *,
    evidence_ids: tuple[UUID, ...],
) -> bool:
    return (
        existing.topic,
        existing.institution,
        existing.campus,
        existing.student_level,
        existing.program,
        existing.catalog_year,
        existing.term,
        existing.session,
        tuple(existing.evidence_block_ids),
    ) == (
        proposed.topic,
        proposed.institution,
        proposed.campus,
        proposed.student_level,
        proposed.program,
        proposed.catalog_year,
        proposed.term,
        proposed.session,
        evidence_ids,
    )


__all__ = [
    "EvidenceIndexer",
    "IndexWriteResult",
    "IndexingConflictError",
    "IndexingEmbeddingError",
    "IndexingError",
    "IndexingFailureReason",
    "IndexingInputError",
    "IndexingPersistenceError",
]
