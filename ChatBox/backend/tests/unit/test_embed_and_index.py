from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.models import SourceChunk, SourceDocument, SourceStatus, SourceType
from app.services.embedding_service import EmbeddingProviderConfig
from ingestion.embed_and_index import build_corpus_version, embed_and_index


@pytest.mark.asyncio
async def test_build_corpus_version_has_prefix_and_timestamp() -> None:
    version = build_corpus_version("manual")

    assert version.startswith("manual-")
    assert len(version) > len("manual-")


@pytest.mark.asyncio
async def test_embed_and_index_is_idempotent_for_unchanged_chunks() -> None:
    document = SourceDocument(
        id=uuid4(),
        title="Example source",
        url="https://example.pnw.edu/policies",
        source_type=SourceType.WEBPAGE,
        campus_scope="both",
        academic_term=None,
        last_updated=datetime.now(timezone.utc).date(),
        status=SourceStatus.ACTIVE,
        reviewed_by="Registrar",
        content_hash="abc123",
        ingestion_status="validated",
    )

    class FakeProvider:
        config = EmbeddingProviderConfig(model_name="test-model", model_version="v1", dimensions=3)

        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.1, 0.2, 0.3] for _ in texts]

    chunk = SourceChunk(
        id=uuid4(),
        document_id=document.id,
        chunk_index=1,
        heading="Policy",
        content="This is an approved policy statement.",
        page_ref="1",
        embedding=[0.1, 0.2, 0.3],
        embedding_model="test-model",
        embedding_dimensions=3,
        corpus_version="manual-1",
    )

    class FakeSession:
        async def execute(self, _statement: object) -> object:
            return type("Result", (), {"scalars": lambda self: [chunk], "scalar_one_or_none": lambda self: None})()

    result = await embed_and_index(
        FakeSession(),
        chunks=[chunk],
        corpus_version="manual-1",
        provider=FakeProvider(),
    )

    assert result[0].id == chunk.id
    assert result[0].corpus_version == "manual-1"
