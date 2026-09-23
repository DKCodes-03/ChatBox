from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import httpx

from app.core.config import Settings, get_settings


class EmbeddingProviderError(RuntimeError):
    """Raised when the embedding provider cannot return a valid response."""


class EmbeddingDimensionError(EmbeddingProviderError):
    """Raised when a provider vector does not match the configured dimensions."""


@dataclass(frozen=True, slots=True)
class EmbeddingProviderConfig:
    model_name: str
    model_version: str
    dimensions: int

    def __post_init__(self) -> None:
        if not self.model_name.strip():
            raise ValueError("embedding model name must not be empty")
        if not self.model_version.strip():
            raise ValueError("embedding model version must not be empty")
        if self.dimensions < 1:
            raise ValueError("embedding dimensions must be positive")

    @classmethod
    def from_settings(cls, settings: Settings) -> "EmbeddingProviderConfig":
        return cls(
            model_name=settings.embedding_model_name,
            model_version=settings.embedding_model_version,
            dimensions=settings.embedding_dimensions,
        )


class OllamaEmbeddingProvider:
    def __init__(
        self,
        config: EmbeddingProviderConfig | None = None,
        *,
        base_url: str | None = None,
        timeout: float | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        settings = get_settings()
        self.config = config or EmbeddingProviderConfig.from_settings(settings)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=(base_url or settings.ollama_base_url).rstrip("/"),
            timeout=timeout or settings.embedding_timeout,
        )

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        inputs = [self._validate_text(text) for text in texts]
        if not inputs:
            return []

        try:
            response = await self._client.post(
                "/api/embed",
                json={"model": self.config.model_name, "input": inputs},
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise EmbeddingProviderError("Ollama embedding request failed") from exc

        embeddings = payload.get("embeddings") if isinstance(payload, dict) else None
        if not isinstance(embeddings, list) or len(embeddings) != len(inputs):
            raise EmbeddingProviderError("Ollama returned an invalid embedding response")

        vectors: list[list[float]] = []
        for embedding in embeddings:
            if not isinstance(embedding, list) or not all(
                isinstance(value, (int, float)) for value in embedding
            ):
                raise EmbeddingProviderError("Ollama returned a malformed embedding vector")
            vector = [float(value) for value in embedding]
            if len(vector) != self.config.dimensions:
                raise EmbeddingDimensionError(
                    f"expected {self.config.dimensions} dimensions, got {len(vector)}"
                )
            vectors.append(vector)
        return vectors

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "OllamaEmbeddingProvider":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    @staticmethod
    def _validate_text(text: str) -> str:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("embedding input text must not be empty")
        return text


def get_embedding_provider() -> OllamaEmbeddingProvider:
    return OllamaEmbeddingProvider()