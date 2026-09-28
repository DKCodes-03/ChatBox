"""Query embeddings must remain temporary and outside persistent models."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import cast

import app.models  # noqa: F401 - imports every persistent model into Base.metadata
import numpy as np
import pytest
from app.models.base import Base
from app.retrieval.embeddings import (
    EMBEDDING_DIMENSIONS,
    MiniLMEmbeddingService,
    QueryVectorClearedError,
    TemporaryQueryVector,
)
from app.retrieval.search import EvidenceSearch, RetrievalScope
from numpy.typing import NDArray
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select


class _Tokenizer:
    @staticmethod
    def tokenize(text: str) -> list[str]:
        return text.split()


class _EmbeddingBackend:
    tokenizer = _Tokenizer()

    @staticmethod
    def get_sentence_embedding_dimension() -> int:
        return EMBEDDING_DIMENSIONS

    @staticmethod
    def encode(
        inputs: Sequence[str],
        *,
        batch_size: int,
        show_progress_bar: bool,
        convert_to_numpy: bool,
        convert_to_tensor: bool,
        normalize_embeddings: bool,
    ) -> NDArray[np.float32]:
        del batch_size, show_progress_bar, convert_to_numpy, convert_to_tensor, normalize_embeddings
        matrix = np.zeros((len(inputs), EMBEDDING_DIMENSIONS), dtype=np.float32)
        matrix[:, 0] = 3.0
        matrix[:, 1] = 4.0
        return matrix


class _Mappings:
    @staticmethod
    def all() -> list[object]:
        return []


class _Result:
    @staticmethod
    def mappings() -> _Mappings:
        return _Mappings()


class _ReadOnlySession:
    def __init__(self) -> None:
        self.statements: list[Select[tuple[object, ...]]] = []

    def execute(self, statement: Select[tuple[object, ...]]) -> _Result:
        self.statements.append(statement)
        return _Result()


def test_temporary_query_vector_is_normalized_then_zeroed_on_scope_exit() -> None:
    service = MiniLMEmbeddingService(
        cache_dir=Path("/unused"),
        backend=_EmbeddingBackend(),
    )
    retained_view: NDArray[np.float32]

    with service.temporary_query_vector("synthetic question") as query_vector:
        retained_view = query_vector.values
        assert query_vector.dimensions == EMBEDDING_DIMENSIONS
        assert np.isclose(np.linalg.norm(retained_view), 1.0)

    assert query_vector.cleared
    assert query_vector.dimensions == 0
    assert np.count_nonzero(retained_view) == 0
    with pytest.raises(QueryVectorClearedError):
        _ = query_vector.values


def test_persistent_metadata_contains_only_public_corpus_vectors() -> None:
    vector_columns = [
        (table.name, column.name)
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, VECTOR)
    ]
    table_names = {table.name.casefold() for table in Base.metadata.tables.values()}
    column_names = {
        column.name.casefold()
        for table in Base.metadata.tables.values()
        for column in table.columns
    }

    assert vector_columns == [("embeddings", "vector")]
    assert all("query" not in name for name in table_names)
    assert all("conversation" not in name for name in table_names)
    assert all("chat" not in name for name in table_names)
    assert column_names.isdisjoint(
        {
            "assistant_message",
            "conversation_id",
            "message_text",
            "query_embedding",
            "query_vector",
            "session_token",
            "student_message",
        }
    )


def test_retrieval_executes_one_read_only_statement_without_persisting_query_vector() -> None:
    values = np.zeros((EMBEDDING_DIMENSIONS,), dtype=np.float32)
    values[0] = 1.0
    query_vector = TemporaryQueryVector(values)
    session = _ReadOnlySession()
    search = EvidenceSearch(
        cast(Session, session),
        model_revision="sentence-transformers/all-MiniLM-L6-v2",
    )

    result = search.search(
        query="registration deadline",
        query_vector=query_vector,
        scope=RetrievalScope(
            topic="registration",
            institution="Purdue University Northwest",
        ),
    )

    assert result.empty
    assert len(session.statements) == 1
    assert session.statements[0].is_select
    assert query_vector.dimensions == EMBEDDING_DIMENSIONS
