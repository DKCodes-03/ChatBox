"""Transactional MiniLM and pgvector loading behavior."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
from app.ingestion.chunking import ChunkingResult
from app.ingestion.indexer import (
    EvidenceIndexer,
    IndexingConflictError,
    IndexingEmbeddingError,
    IndexingFailureReason,
    IndexingInputError,
    IndexingPersistenceError,
)
from app.models.enums import ExtractionStatus
from app.models.sources import Applicability, Embedding, EvidenceBlock, SourceVersion
from app.retrieval.embeddings import EMBEDDING_DIMENSIONS, MODEL_NAME, MiniLMEmbeddingService
from numpy.typing import NDArray
from pytest import MonkeyPatch
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


class _Tokenizer:
    @staticmethod
    def tokenize(text: str) -> list[str]:
        return text.split()


class _EmbeddingBackend:
    tokenizer = _Tokenizer()

    def __init__(self, *, invalid_shape: bool = False) -> None:
        self.invalid_shape = invalid_shape
        self.calls: list[tuple[tuple[str, ...], bool]] = []

    @staticmethod
    def get_sentence_embedding_dimension() -> int:
        return EMBEDDING_DIMENSIONS

    def encode(
        self,
        inputs: Sequence[str],
        *,
        batch_size: int,
        show_progress_bar: bool,
        convert_to_numpy: bool,
        convert_to_tensor: bool,
        normalize_embeddings: bool,
    ) -> NDArray[np.float32]:
        del batch_size, show_progress_bar, convert_to_numpy, convert_to_tensor
        self.calls.append((tuple(inputs), normalize_embeddings))
        dimensions = EMBEDDING_DIMENSIONS - 1 if self.invalid_shape else EMBEDDING_DIMENSIONS
        matrix = np.zeros((len(inputs), dimensions), dtype=np.float32)
        matrix[:, 0] = 3.0
        matrix[:, 1] = 4.0
        return matrix


class _ScalarRows:
    def __init__(self, rows: Sequence[object]) -> None:
        self._rows = rows

    def all(self) -> Sequence[object]:
        return self._rows


def _service(backend: _EmbeddingBackend) -> MiniLMEmbeddingService:
    return MiniLMEmbeddingService(cache_dir=Path("/unused"), backend=backend)


def _batch() -> tuple[SourceVersion, ChunkingResult]:
    version = SourceVersion(
        id=uuid4(),
        source_id=uuid4(),
        content_sha256="a" * 64,
        content="synthetic public content",
        fetched_at=datetime(2030, 1, 15, tzinfo=UTC),
        extraction_status=ExtractionStatus.COMPLETE,
        parser_version="structured-extractor-v1",
    )
    block_specs = (
        (["Registration", "Add a class"], "Submit the published add form."),
        (["Registration", "Drop a class"], "Submit the published drop form."),
    )
    blocks = tuple(
        EvidenceBlock(
            id=uuid4(),
            version_id=version.id,
            ordinal=ordinal,
            heading_path=heading_path,
            anchor=f"step-{ordinal + 1}",
            text=text,
            structured_content={
                "embedding_wordpiece_count": len(" ".join((*heading_path, text)).split())
            },
            topic_key="registration",
            scope={},
        )
        for ordinal, (heading_path, text) in enumerate(block_specs)
    )
    applicability = Applicability(
        version_id=version.id,
        topic="registration",
        institution="Purdue University Northwest",
        campus="Hammond",
        term="Fall",
        evidence_block_ids=[block.id for block in blocks],
    )
    counts = tuple(
        len(" ".join((*heading_path, text)).split()) for heading_path, text in block_specs
    )
    return version, ChunkingResult(
        blocks=blocks,
        applicability=applicability,
        wordpiece_counts=counts,
    )


def _fake_session(
    monkeypatch: MonkeyPatch,
    *,
    scalar_batches: Sequence[Sequence[object]],
    scalar_value: object | None = None,
    flush_error: bool = False,
) -> tuple[Session, list[object], list[str]]:
    session = Session()
    queued = list(scalar_batches)
    added: list[object] = []
    events: list[str] = []

    def scalars(_statement: object) -> _ScalarRows:
        return _ScalarRows(queued.pop(0))

    def scalar(_statement: object) -> object | None:
        return scalar_value

    def add_all(instances: Sequence[object]) -> None:
        events.append("add_all")
        added.extend(instances)

    def flush(_objects: object = None) -> None:
        events.append("flush")
        if flush_error:
            raise IntegrityError("insert", {}, RuntimeError("synthetic constraint failure"))

    def commit() -> None:
        raise AssertionError("the caller owns the transaction")

    monkeypatch.setattr(session, "scalars", scalars)
    monkeypatch.setattr(session, "scalar", scalar)
    monkeypatch.setattr(session, "add_all", add_all)
    monkeypatch.setattr(session, "flush", flush)
    monkeypatch.setattr(session, "commit", commit)
    return session, added, events


def test_new_batch_stages_normalized_vectors_and_model_metadata_without_commit(
    monkeypatch: MonkeyPatch,
) -> None:
    version, chunks = _batch()
    backend = _EmbeddingBackend()
    session, added, events = _fake_session(monkeypatch, scalar_batches=((),))

    result = EvidenceIndexer(session, embedding_service=_service(backend)).index(
        version=version,
        chunks=chunks,
    )

    assert result.created_blocks is True
    assert result.created_embeddings is True
    assert result.model_revision == MODEL_NAME
    assert result.dimensions == EMBEDDING_DIMENSIONS
    assert events == ["add_all", "flush"]
    assert added == [*chunks.blocks, chunks.applicability, *result.embeddings]
    assert backend.calls == [
        (
            (
                "Registration\nAdd a class\nSubmit the published add form.",
                "Registration\nDrop a class\nSubmit the published drop form.",
            ),
            True,
        )
    ]
    for block, embedding in zip(chunks.blocks, result.embeddings, strict=True):
        assert embedding.block_id == block.id
        assert embedding.model_revision == MODEL_NAME
        assert embedding.dimensions == EMBEDDING_DIMENSIONS
        assert np.isclose(np.linalg.norm(embedding.vector), 1.0)


def test_invalid_embedding_result_is_rejected_before_any_database_write(
    monkeypatch: MonkeyPatch,
) -> None:
    version, chunks = _batch()
    session, added, events = _fake_session(monkeypatch, scalar_batches=((),))

    with pytest.raises(IndexingEmbeddingError) as captured:
        EvidenceIndexer(
            session,
            embedding_service=_service(_EmbeddingBackend(invalid_shape=True)),
        ).index(version=version, chunks=chunks)

    assert captured.value.reason is IndexingFailureReason.EMBEDDING_FAILED
    assert added == []
    assert events == []


def test_inconsistent_chunk_metadata_is_rejected_before_embedding_or_write(
    monkeypatch: MonkeyPatch,
) -> None:
    version, chunks = _batch()
    backend = _EmbeddingBackend()
    session, added, events = _fake_session(monkeypatch, scalar_batches=())
    chunks.applicability.evidence_block_ids.reverse()

    with pytest.raises(IndexingInputError) as captured:
        EvidenceIndexer(session, embedding_service=_service(backend)).index(
            version=version,
            chunks=chunks,
        )

    assert captured.value.reason is IndexingFailureReason.INVALID_BATCH
    assert backend.calls == []
    assert added == []
    assert events == []


def test_constraint_failure_is_sanitized_for_caller_rollback(monkeypatch: MonkeyPatch) -> None:
    version, chunks = _batch()
    session, _, events = _fake_session(
        monkeypatch,
        scalar_batches=((),),
        flush_error=True,
    )

    with pytest.raises(IndexingPersistenceError) as captured:
        EvidenceIndexer(session, embedding_service=_service(_EmbeddingBackend())).index(
            version=version,
            chunks=chunks,
        )

    assert captured.value.reason is IndexingFailureReason.PERSISTENCE_FAILED
    assert str(captured.value) == "persistence_failed"
    assert events == ["add_all", "flush"]


def test_complete_existing_batch_is_idempotently_reused(monkeypatch: MonkeyPatch) -> None:
    version, chunks = _batch()
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[0] = 1.0
    embeddings = tuple(
        Embedding(
            block_id=block.id,
            model_revision=MODEL_NAME,
            dimensions=EMBEDDING_DIMENSIONS,
            vector=list(vector),
        )
        for block in chunks.blocks
    )
    backend = _EmbeddingBackend()
    session, added, events = _fake_session(
        monkeypatch,
        scalar_batches=(chunks.blocks, embeddings),
        scalar_value=chunks.applicability,
    )

    result = EvidenceIndexer(session, embedding_service=_service(backend)).index(
        version=version,
        chunks=chunks,
    )

    assert result.blocks == chunks.blocks
    assert result.embeddings == embeddings
    assert result.created_blocks is False
    assert result.created_embeddings is False
    assert backend.calls == []
    assert added == []
    assert events == []


def test_partial_existing_vector_batch_is_rejected_instead_of_replaced(
    monkeypatch: MonkeyPatch,
) -> None:
    version, chunks = _batch()
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[0] = 1.0
    partial = (
        Embedding(
            block_id=chunks.blocks[0].id,
            model_revision=MODEL_NAME,
            dimensions=EMBEDDING_DIMENSIONS,
            vector=vector,
        ),
    )
    backend = _EmbeddingBackend()
    session, added, events = _fake_session(
        monkeypatch,
        scalar_batches=(chunks.blocks, partial),
        scalar_value=chunks.applicability,
    )

    with pytest.raises(IndexingConflictError) as captured:
        EvidenceIndexer(session, embedding_service=_service(backend)).index(
            version=version,
            chunks=chunks,
        )

    assert captured.value.reason is IndexingFailureReason.EXISTING_INDEX_CONFLICT
    assert backend.calls == []
    assert added == []
    assert events == []
