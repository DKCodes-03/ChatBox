"""Deterministic transient embeddings for the synthetic Compose test profile."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from contextlib import contextmanager

import numpy as np
from numpy.typing import NDArray

from app.retrieval.embeddings import EMBEDDING_DIMENSIONS, TemporaryQueryVector

FloatVector = NDArray[np.float32]


def deterministic_vector(text: str) -> FloatVector:
    """Create a stable normalized vector without loading or downloading a model."""

    if not isinstance(text, str) or not text.strip():
        raise ValueError("deterministic embedding input is invalid")
    digest = hashlib.sha256(text.strip().encode("utf-8")).digest()
    repeated = (digest * ((EMBEDDING_DIMENSIONS // len(digest)) + 1))[:EMBEDDING_DIMENSIONS]
    values = np.frombuffer(repeated, dtype=np.uint8).astype(np.float32) - 127.5
    norm = float(np.linalg.norm(values))
    if not np.isfinite(norm) or norm <= 0:
        raise ValueError("deterministic embedding could not be normalized")
    return np.ascontiguousarray(values / norm, dtype=np.float32)


class DeterministicEmbeddingService:
    """Expose only the temporary query-vector boundary used by the coordinator."""

    @contextmanager
    def temporary_query_vector(self, query: str) -> Iterator[TemporaryQueryVector]:
        temporary = TemporaryQueryVector(deterministic_vector(query))
        try:
            yield temporary
        finally:
            temporary.clear()
