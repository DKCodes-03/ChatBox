from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models import (
    CorpusBuild,
    CorpusBuildStatus,
    IngestionStatus,
    SourceChunk,
    SourceDocument,
    SourceStatus,
)
from app.services.embedding_service import EmbeddingProviderConfig, OllamaEmbeddingProvider
from ingestion.build_corpus import run_corpus_build


@pytest.fixture
async def test_session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    database_url = os.environ.get("CHATBOX_TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("set CHATBOX_TEST_DATABASE_URL to run PostgreSQL ingestion integration test")
    database_name = make_url(database_url).database
    if database_name is None or not database_name.endswith("_test"):
        pytest.fail("CHATBOX_TEST_DATABASE_URL must point to a dedicated database ending in _test")

    engine = create_async_engine(database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield factory
        finally:
            await transaction.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_manifest_ingestion_persists_vectors_and_skips_unchanged_pages(
    tmp_path: Path,
    test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    settings = get_settings()
    source_url = f"https://pnw.edu/integration/{uuid4().hex}/"
    manifest_path = tmp_path / "sources.yaml"
    manifest_path.write_text(
        yaml.safe_dump(
            {
                "manifest_version": 1,
                "defaults": {
                    "allowed_hosts": ["pnw.edu"],
                    "review": {"default_cadence_days": 30},
                    "follow_links": {"allowed_content_types": ["text/html"]},
                },
                "sources": [
                    {
                        "id": "integration-source",
                        "title": "Integration Registration Guide",
                        "url": source_url,
                        "source_type": "webpage",
                        "campus_scope": "both",
                        "academic_term": "current",
                        "reviewer": "integration-test",
                        "review_status": "active",
                        "last_reviewed": datetime.now(UTC).date().isoformat(),
                        "review_cadence_days": 30,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    page_requests = 0

    async def source_handler(_request: httpx.Request) -> httpx.Response:
        nonlocal page_requests
        page_requests += 1
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=(
                b"<html><head><title>Registration Guide</title></head>"
                b"<body><main><h1>Registration</h1>"
                b"<p>Registration opens on August 1.</p></main></body></html>"
            ),
        )

    embedded_batches: list[list[str]] = []

    async def embedding_handler(request: httpx.Request) -> httpx.Response:
        payload: dict[str, Any] = json.loads(await request.aread())
        texts = payload["input"]
        embedded_batches.append(texts)
        vectors = [[0.125] * settings.embedding_dimensions for _ in texts]
        return httpx.Response(200, json={"embeddings": vectors})

    embedding_client = httpx.AsyncClient(
        base_url="http://ollama.test",
        transport=httpx.MockTransport(embedding_handler),
    )
    provider = OllamaEmbeddingProvider(
        config=EmbeddingProviderConfig(
            model_name=settings.embedding_model_name,
            model_version=settings.embedding_model_version,
            dimensions=settings.embedding_dimensions,
        ),
        client=embedding_client,
    )
    source_client = httpx.AsyncClient(transport=httpx.MockTransport(source_handler))

    build, skipped = await run_corpus_build(
        manifest_path,
        source_client=source_client,
        db_session_factory=test_session_factory,
        embedding_provider=provider,
    )
    await embedding_client.aclose()

    assert skipped == []
    assert build is not None
    assert build.status is CorpusBuildStatus.PROMOTED
    assert build.source_count == 1
    assert build.chunk_count == 1
    assert len(embedded_batches) == 1

    async with test_session_factory() as session:
        document_result = await session.execute(
            select(SourceDocument).where(SourceDocument.url == source_url)
        )
        document = document_result.scalar_one()
        assert document.status is SourceStatus.ACTIVE
        assert document.ingestion_status is IngestionStatus.VALIDATED

        chunk_result = await session.execute(
            select(SourceChunk).where(SourceChunk.document_id == document.id)
        )
        chunk = chunk_result.scalar_one()
        assert "Registration opens on August 1" in chunk.content
        assert chunk.corpus_version == build.version
        assert chunk.embedding is not None
        assert len(chunk.embedding) == settings.embedding_dimensions

        build_count_result = await session.execute(select(CorpusBuild.id))
        build_count_before_repeat = len(build_count_result.scalars().all())

    repeat_client = httpx.AsyncClient(transport=httpx.MockTransport(source_handler))
    repeated_build, repeated_skipped = await run_corpus_build(
        manifest_path,
        source_client=repeat_client,
        db_session_factory=test_session_factory,
    )

    assert repeated_build is None
    assert repeated_skipped == []
    assert page_requests == 2
    assert len(embedded_batches) == 1

    async with test_session_factory() as session:
        build_count_result = await session.execute(select(CorpusBuild.id))
        assert len(build_count_result.scalars().all()) == build_count_before_repeat