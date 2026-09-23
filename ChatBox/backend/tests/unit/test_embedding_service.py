import httpx
import pytest

from app.services.embedding_service import (
    EmbeddingDimensionError,
    EmbeddingProviderConfig,
    OllamaEmbeddingProvider,
)


@pytest.mark.asyncio
async def test_ollama_provider_returns_configured_vector_dimensions() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2, 0.3]]})

    config = EmbeddingProviderConfig("test-model", "test-version", 3)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    ) as client:
        provider = OllamaEmbeddingProvider(config, client=client)
        assert await provider.embed_one("test text") == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_ollama_provider_rejects_dimension_mismatch() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2]]})

    config = EmbeddingProviderConfig("test-model", "test-version", 3)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="http://test"
    ) as client:
        provider = OllamaEmbeddingProvider(config, client=client)
        with pytest.raises(EmbeddingDimensionError):
            await provider.embed_one("test text")


def test_embedding_config_rejects_invalid_dimensions() -> None:
    with pytest.raises(ValueError, match="dimensions must be positive"):
        EmbeddingProviderConfig("test-model", "test-version", 0)