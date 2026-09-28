"""Local MiniLM embeddings with bounded evidence and transient query vectors."""

from __future__ import annotations

import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

import numpy as np
from app.config import Settings
from numpy.typing import NDArray

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIMENSIONS = 384
MAX_EVIDENCE_WORDPIECES = 220
MAX_QUERY_CHARACTERS = 4_000
DEFAULT_BATCH_SIZE = 32

FloatVector = NDArray[np.float32]


class EmbeddingError(RuntimeError):
    """Base class for errors that do not expose input text."""


class EmbeddingModelUnavailableError(EmbeddingError):
    def __init__(self) -> None:
        super().__init__("embedding model is unavailable")


class EmbeddingModelContractError(EmbeddingError):
    def __init__(self) -> None:
        super().__init__("embedding model returned an invalid result")


class InvalidEmbeddingInputError(EmbeddingError):
    def __init__(self) -> None:
        super().__init__("embedding input is invalid")


class EvidenceUnitTooLargeError(EmbeddingError):
    def __init__(self, *, wordpiece_count: int, maximum: int) -> None:
        super().__init__("evidence unit exceeds the wordpiece limit")
        self.wordpiece_count = wordpiece_count
        self.maximum = maximum


class QueryVectorClearedError(EmbeddingError):
    def __init__(self) -> None:
        super().__init__("temporary query vector has been cleared")


class _WordpieceTokenizer(Protocol):
    def tokenize(self, text: str) -> list[str]: ...


class _EmbeddingBackend(Protocol):
    @property
    def tokenizer(self) -> _WordpieceTokenizer: ...

    def get_sentence_embedding_dimension(self) -> int | None: ...

    def encode(
        self,
        inputs: Sequence[str],
        *,
        batch_size: int,
        show_progress_bar: bool,
        convert_to_numpy: bool,
        convert_to_tensor: bool,
        normalize_embeddings: bool,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class EvidenceUnit:
    """Public-source text prepared for one embedding, including its headings."""

    text: str
    wordpiece_count: int


@dataclass(frozen=True, slots=True)
class EmbeddingVector:
    """An immutable, normalized corpus vector suitable for later persistence."""

    values: tuple[float, ...]

    @property
    def dimensions(self) -> int:
        return len(self.values)


class TemporaryQueryVector:
    """A query vector whose owned memory is zeroed when its request scope ends."""

    def __init__(self, values: FloatVector) -> None:
        self._lock = threading.Lock()
        self._values = np.array(values, dtype=np.float32, copy=True)
        self._cleared = False

    @property
    def dimensions(self) -> int:
        with self._lock:
            return 0 if self._cleared else int(self._values.size)

    @property
    def cleared(self) -> bool:
        with self._lock:
            return self._cleared

    @property
    def values(self) -> FloatVector:
        """Return the owned array; callers must not retain copies beyond request scope."""

        with self._lock:
            if self._cleared:
                raise QueryVectorClearedError()
            view = self._values.view()
            view.flags.writeable = False
            return view

    def clear(self) -> None:
        """Zero the allocation before releasing the object's reference to it."""

        with self._lock:
            if self._cleared:
                return
            self._values.fill(0.0)
            self._values = np.empty((0,), dtype=np.float32)
            self._cleared = True

    def __repr__(self) -> str:
        return f"TemporaryQueryVector(dimensions={self.dimensions}, cleared={self.cleared})"


class MiniLMEmbeddingService:
    """Load one offline MiniLM model for bounded corpus and query embedding."""

    def __init__(
        self,
        *,
        cache_dir: Path,
        model_name: str = MODEL_NAME,
        dimensions: int = EMBEDDING_DIMENSIONS,
        max_evidence_wordpieces: int = MAX_EVIDENCE_WORDPIECES,
        batch_size: int = DEFAULT_BATCH_SIZE,
        backend: _EmbeddingBackend | None = None,
    ) -> None:
        if model_name != MODEL_NAME:
            raise ValueError("only the approved MiniLM embedding model is supported")
        if dimensions != EMBEDDING_DIMENSIONS:
            raise ValueError("MiniLM embeddings must contain 384 dimensions")
        if max_evidence_wordpieces != MAX_EVIDENCE_WORDPIECES:
            raise ValueError("evidence units must be limited to 220 wordpieces")
        if batch_size < 1:
            raise ValueError("embedding batch size must be positive")

        self._cache_dir = cache_dir
        self._model_name = model_name
        self._dimensions = dimensions
        self._max_evidence_wordpieces = max_evidence_wordpieces
        self._batch_size = batch_size
        self._backend = backend
        self._load_lock = threading.Lock()
        self._encode_lock = threading.Lock()

        if backend is not None:
            self._validate_backend(backend)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        backend: _EmbeddingBackend | None = None,
    ) -> MiniLMEmbeddingService:
        return cls(
            cache_dir=settings.embedding_model_cache_dir,
            model_name=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            max_evidence_wordpieces=settings.evidence_max_wordpieces,
            backend=backend,
        )

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def max_evidence_wordpieces(self) -> int:
        return self._max_evidence_wordpieces

    def count_wordpieces(self, text: str) -> int:
        cleaned = self._validated_text(text)
        try:
            count = len(self._get_backend().tokenizer.tokenize(cleaned))
        except EmbeddingError:
            raise
        except Exception:
            raise EmbeddingModelContractError() from None
        if count < 1:
            raise EmbeddingModelContractError()
        return count

    def prepare_evidence_unit(
        self,
        *,
        text: str,
        heading_path: Sequence[str] = (),
    ) -> EvidenceUnit:
        """Combine heading context with public text and enforce the 220-piece limit."""

        body = self._validated_text(text)
        headings = tuple(self._validated_text(heading) for heading in heading_path)
        complete_text = "\n".join((*headings, body))
        wordpiece_count = self.count_wordpieces(complete_text)
        self._enforce_evidence_limit(wordpiece_count)
        return EvidenceUnit(text=complete_text, wordpiece_count=wordpiece_count)

    def embed_evidence_unit(self, unit: EvidenceUnit) -> EmbeddingVector:
        return self.embed_evidence_units((unit,))[0]

    def embed_evidence_units(
        self,
        units: Sequence[EvidenceUnit],
    ) -> tuple[EmbeddingVector, ...]:
        """Embed complete public evidence units and return normalized immutable vectors."""

        if not units:
            raise InvalidEmbeddingInputError()

        texts: list[str] = []
        for unit in units:
            text = self._validated_text(unit.text)
            wordpiece_count = self.count_wordpieces(text)
            self._enforce_evidence_limit(wordpiece_count)
            if wordpiece_count != unit.wordpiece_count:
                raise InvalidEmbeddingInputError()
            texts.append(text)

        matrix = self._encode(texts)
        return tuple(EmbeddingVector(values=tuple(float(value) for value in row)) for row in matrix)

    @contextmanager
    def temporary_query_vector(self, query: str) -> Iterator[TemporaryQueryVector]:
        """Yield a normalized query vector and always zero it when the scope exits."""

        cleaned_query = self._validated_query(query)
        matrix = self._encode((cleaned_query,))
        temporary = TemporaryQueryVector(matrix[0])
        matrix.fill(0.0)
        matrix = np.empty((0, self._dimensions), dtype=np.float32)
        query = ""
        cleaned_query = ""
        try:
            yield temporary
        finally:
            temporary.clear()

    @staticmethod
    def _validated_text(text: str) -> str:
        if not isinstance(text, str):
            raise InvalidEmbeddingInputError()
        cleaned = text.strip()
        if not cleaned:
            raise InvalidEmbeddingInputError()
        return cleaned

    @classmethod
    def _validated_query(cls, query: str) -> str:
        cleaned = cls._validated_text(query)
        if len(cleaned) > MAX_QUERY_CHARACTERS:
            raise InvalidEmbeddingInputError()
        return cleaned

    def _enforce_evidence_limit(self, wordpiece_count: int) -> None:
        if wordpiece_count > self._max_evidence_wordpieces:
            raise EvidenceUnitTooLargeError(
                wordpiece_count=wordpiece_count,
                maximum=self._max_evidence_wordpieces,
            )

    def _get_backend(self) -> _EmbeddingBackend:
        backend = self._backend
        if backend is not None:
            return backend

        with self._load_lock:
            backend = self._backend
            if backend is None:
                backend = self._load_backend()
                self._validate_backend(backend)
                self._backend = backend
        return backend

    def _load_backend(self) -> _EmbeddingBackend:
        try:
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(
                self._model_name,
                cache_folder=str(self._cache_dir),
                local_files_only=True,
                trust_remote_code=False,
            )
        except Exception:
            raise EmbeddingModelUnavailableError() from None
        return cast(_EmbeddingBackend, model)

    def _validate_backend(self, backend: _EmbeddingBackend) -> None:
        try:
            dimensions = backend.get_sentence_embedding_dimension()
            tokenizer = backend.tokenizer
        except Exception:
            raise EmbeddingModelContractError() from None
        if dimensions != self._dimensions or tokenizer is None:
            raise EmbeddingModelContractError()

    def _encode(self, texts: Sequence[str]) -> FloatVector:
        try:
            with self._encode_lock:
                result = self._get_backend().encode(
                    texts,
                    batch_size=self._batch_size,
                    show_progress_bar=False,
                    convert_to_numpy=True,
                    convert_to_tensor=False,
                    normalize_embeddings=True,
                )
            matrix = np.asarray(result, dtype=np.float32)
        except EmbeddingError:
            raise
        except Exception:
            raise EmbeddingModelContractError() from None

        if matrix.ndim == 1 and len(texts) == 1:
            matrix = matrix.reshape(1, -1)
        if matrix.shape != (len(texts), self._dimensions) or not np.isfinite(matrix).all():
            raise EmbeddingModelContractError()

        norms = np.linalg.norm(matrix, axis=1)
        if not np.isfinite(norms).all() or np.any(norms <= 0.0):
            raise EmbeddingModelContractError()

        normalized = matrix / norms[:, np.newaxis]
        return np.ascontiguousarray(normalized, dtype=np.float32)
