from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SourceChunk
from app.services.embedding_service import OllamaEmbeddingProvider, get_embedding_provider


def build_corpus_version(prefix: str = "corpus") -> str:
    """Create a unique, timeline-based corpus version identifier."""
    normalized = (prefix or "corpus").strip() or "corpus"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{normalized}-{timestamp}"


async def _persist_chunks(session: AsyncSession, chunks: Sequence[SourceChunk]) -> None:
    for chunk in chunks:
        if hasattr(session, "merge"):
            await session.merge(chunk)
    if hasattr(session, "flush"):
        await session.flush()


async def embed_and_index(
    session: AsyncSession,
    *,
    chunks: Sequence[SourceChunk],
    corpus_version: str | None = None,
    provider: OllamaEmbeddingProvider | None = None,
) -> list[SourceChunk]:
    """Embed validated chunks and store the vector metadata with a corpus version.

    The operation is idempotent: chunks that already have an embedding for the same
    corpus version and model dimensions are left unchanged, preventing duplicate
    work on repeated ingestion runs.
    """
    if corpus_version is None:
        corpus_version = build_corpus_version()

    if not chunks:
        return []

    effective_provider = provider or get_embedding_provider()
    config = effective_provider.config

    pending: list[SourceChunk] = []
    texts: list[str] = []
    for chunk in chunks:
        if not isinstance(chunk.content, str) or not chunk.content.strip():
            raise ValueError(f"chunk {chunk.id!s} content must not be empty")

        if (
            chunk.corpus_version == corpus_version
            and chunk.embedding is not None
            and chunk.embedding_model == config.model_name
            and chunk.embedding_dimensions == config.dimensions
        ):
            continue

        pending.append(chunk)
        texts.append(chunk.content)

    if not texts:
        return list(chunks)

    vectors = await effective_provider.embed(texts)
    for chunk, vector in zip(pending, vectors, strict=True):
        chunk.embedding = vector
        chunk.embedding_model = config.model_name
        chunk.embedding_dimensions = config.dimensions
        chunk.corpus_version = corpus_version

    await _persist_chunks(session, pending)
    return list(chunks)


async def index_validated_chunks(
    session: AsyncSession,
    *,
    chunks: Sequence[SourceChunk],
    corpus_version: str | None = None,
    provider: OllamaEmbeddingProvider | None = None,
) -> list[SourceChunk]:
    return await embed_and_index(
        session,
        chunks=chunks,
        corpus_version=corpus_version,
        provider=provider,
    )


async def main() -> None:
    """CLI entrypoint for local corpus embedding jobs."""
    raise NotImplementedError(
        "This ingestion command is designed to be wired to the database session layer "
        "in the application runtime."
    )


if __name__ == "__main__":
    raise SystemExit("Use the application runtime to execute this ingestion job.")
