"""End-to-end vector preparation against synthetic public-source fixtures."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import UUID

import httpx
import numpy as np
import pytest
from app.config import DatabaseRole, Settings
from app.ingestion.chunking import ChunkingContext, ChunkingInputError, SemanticChunker
from app.ingestion.discovery import (
    DiscoveredDocument,
    DiscoveryPolicy,
    SourceDiscovery,
    SourceSeed,
)
from app.ingestion.extract import ExtractionFailureReason, StructuredExtractor
from app.ingestion.indexer import EvidenceIndexer, IndexingEmbeddingError
from app.ingestion.qualification import (
    QualificationEvidence,
    QualificationFailureReason,
    SourceQualificationService,
)
from app.ingestion.refresh import RefreshFailureReason, SourceRefreshService
from app.ingestion.versions import SourceVersionService, content_sha256
from app.models.enums import (
    ExtractionStatus,
    MediaType,
    QualificationStatus,
    SourceStatus,
)
from app.models.sources import Embedding, EvidenceBlock, Qualification, Source, SourceVersion
from app.retrieval.embeddings import EMBEDDING_DIMENSIONS, MiniLMEmbeddingService
from app.testing.embedding import deterministic_vector
from numpy.typing import NDArray
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "corpus" / "ingestion"
OBSERVED_AT = datetime(2030, 1, 16, 13, tzinfo=UTC)
REFRESHED_AT = OBSERVED_AT + timedelta(hours=24)
DATABASE_INTEGRATION_ENABLED = os.environ.get("DATABASE_INTEGRATION_TESTS") == "true"
requires_database = pytest.mark.skipif(
    not DATABASE_INTEGRATION_ENABLED,
    reason="run through the Compose test-vector-pipeline service",
)


def _load_json(name: str) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8")),
    )


METADATA = _load_json("fixture-metadata.json")
EXPECTED = _load_json("expected-results.json")
SOURCES = cast(tuple[dict[str, Any], ...], tuple(METADATA["sources"]))
SOURCES_BY_URL = {cast(str, item["url"]): item for item in SOURCES}
SOURCES_BY_FILE = {cast(str, item["fixture_file"]): item for item in SOURCES}


class _Tokenizer:
    @staticmethod
    def tokenize(text: str) -> list[str]:
        return text.split()


class _EmbeddingBackend:
    tokenizer = _Tokenizer()

    def __init__(self, *, invalid_shape: bool = False) -> None:
        self.invalid_shape = invalid_shape

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
        assert normalize_embeddings
        matrix = np.stack(tuple(deterministic_vector(text) for text in inputs))
        if self.invalid_shape:
            return matrix[:, :-1]
        return matrix


def _embedding_service(*, invalid_shape: bool = False) -> MiniLMEmbeddingService:
    return MiniLMEmbeddingService(
        cache_dir=Path("/unused"),
        backend=_EmbeddingBackend(invalid_shape=invalid_shape),
    )


def _fixture_document(
    source: dict[str, Any],
    *,
    fixture_file: str | None = None,
) -> DiscoveredDocument:
    filename = fixture_file or cast(str, source["fixture_file"])
    url = cast(str, source["url"])
    return DiscoveredDocument(
        requested_url=url,
        canonical_url=url,
        title=cast(str, source["title"]),
        topics=(
            cast(str, source["applicability"]["topic"])
            if source["applicability"] is not None
            else "synthetic_malformed_table",
        ),
        media_type=MediaType(cast(str, source["media_type"])),
        content=(FIXTURE_DIR / filename).read_bytes(),
        depth=0,
        parent_url=None,
        redirect_chain=(),
    )


def _response_for(request: httpx.Request) -> httpx.Response:
    source = SOURCES_BY_URL.get(str(request.url))
    if source is None:
        return httpx.Response(404, request=request)
    media_type = MediaType(cast(str, source["media_type"]))
    content_type = "application/pdf" if media_type is MediaType.PDF else "text/html; charset=utf-8"
    return httpx.Response(
        200,
        headers={"content-type": content_type},
        content=(FIXTURE_DIR / cast(str, source["fixture_file"])).read_bytes(),
        request=request,
    )


def _discover_linked_documents() -> tuple[DiscoveredDocument, ...]:
    scenario = cast(dict[str, Any], EXPECTED["scenarios"]["linked_documents"])
    root_url = cast(str, scenario["root_url"])
    root = SOURCES_BY_URL[root_url]
    client = httpx.Client(transport=httpx.MockTransport(_response_for))
    discovery = SourceDiscovery(
        DiscoveryPolicy(max_crawl_depth=3, max_urls_per_run=100),
        client=client,
        resolver=lambda _host, _port: ("93.184.216.34",),
    )
    try:
        report = discovery.discover(
            (
                SourceSeed(
                    url=root_url,
                    title=cast(str, root["title"]),
                    topics=("synthetic_linked_procedure",),
                    allowed_child_hosts=("www.pnw.edu",),
                ),
            )
        )
    finally:
        discovery.close()
        client.close()

    assert report.issues == ()
    assert not report.truncated
    assert set(report.attempted_urls) == set(scenario["expected_discovered_urls"])
    assert {document.canonical_url for document in report.documents} == set(
        scenario["expected_discovered_urls"]
    )
    return report.documents


def _source_for(document: DiscoveredDocument, extracted_title: str) -> Source:
    metadata = SOURCES_BY_URL[document.canonical_url]
    return Source(
        id=UUID(cast(str, metadata["source_id"])),
        canonical_url=document.canonical_url,
        title=extracted_title,
        media_type=document.media_type,
        status=SourceStatus.CANDIDATE,
    )


def _context_for(source_metadata: dict[str, Any]) -> ChunkingContext:
    applicability = cast(dict[str, Any], source_metadata["applicability"])
    return ChunkingContext(
        topic_key=cast(str, applicability["topic"]),
        institution=cast(str, applicability["institution"]),
        campus=cast(str | None, applicability.get("campus")),
        student_level=cast(str | None, applicability.get("student_level")),
        program=cast(str | None, applicability.get("program")),
        catalog_year=cast(str | None, applicability.get("catalog_year")),
        term=cast(str | None, applicability.get("term")),
        session=cast(str | None, applicability.get("session")),
    )


def _prepare_and_qualify(
    session: Session,
    document: DiscoveredDocument,
    *,
    observed_at: datetime,
    embedding_service: MiniLMEmbeddingService,
) -> tuple[Source, SourceVersion, tuple[EvidenceBlock, ...], tuple[Embedding, ...]]:
    extraction = StructuredExtractor().extract(document)
    assert extraction.complete
    source = _source_for(document, extraction.title)
    session.add(source)
    session.flush()

    write = SourceVersionService(session, clock=lambda: observed_at).record(
        source=source,
        document=document,
        extraction=extraction,
    )
    assert write.created
    assert write.extraction_status is ExtractionStatus.COMPLETE
    metadata = SOURCES_BY_URL[document.canonical_url]
    assert write.version.content_sha256 == metadata["content_sha256"]

    chunks = SemanticChunker(embedding_service.count_wordpieces).chunk(
        version=write.version,
        document=extraction,
        context=_context_for(metadata),
    )
    indexed = EvidenceIndexer(session, embedding_service=embedding_service).index(
        version=write.version,
        chunks=chunks,
    )
    result = SourceQualificationService(session, clock=lambda: observed_at).qualify(
        source=source,
        version=write.version,
        evidence=QualificationEvidence(document=document, observed_at=observed_at),
    )
    assert result.eligible
    assert result.reason_codes == ()
    return source, write.version, indexed.blocks, indexed.embeddings


@pytest.fixture
def governance_session() -> Iterator[Session]:
    if not DATABASE_INTEGRATION_ENABLED:
        pytest.skip("run through the Compose test-vector-pipeline service")
    engine = create_engine(Settings().database_url(DatabaseRole.GOVERNANCE))
    connection = engine.connect()
    outer_transaction = connection.begin()
    session = Session(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        yield session
    finally:
        session.close()
        if outer_transaction.is_active:
            outer_transaction.rollback()
        connection.close()
        engine.dispose()


def test_discovery_fetch_hash_and_extraction_span_html_link_and_pdf() -> None:
    documents = _discover_linked_documents()
    extracted = tuple(StructuredExtractor().extract(document) for document in documents)
    scenario = cast(dict[str, Any], EXPECTED["scenarios"]["pdf"])

    assert [document.depth for document in documents] == [0, 1, 1]
    assert documents[1].parent_url == documents[0].canonical_url
    assert documents[2].parent_url == documents[0].canonical_url
    for document, result in zip(documents, extracted, strict=True):
        metadata = SOURCES_BY_URL[document.canonical_url]
        assert content_sha256(document) == metadata["content_sha256"]
        assert result.complete
        assert result.issues == ()

    pdf = next(item for item in extracted if item.media_type is MediaType.PDF)
    assert {unit.page for unit in pdf.units if unit.page is not None} == set(
        scenario["expected_pages"]
    )
    assert all(marker in pdf.text for marker in scenario["text_contains"])


def test_extraction_and_chunking_preserve_scope_relationships_and_quarantine_input() -> None:
    service = _embedding_service()
    extractor = StructuredExtractor()

    for campus, filename in (("Hammond", "campus-hammond.html"), ("Westville", "campus-westville.html")):
        source_metadata = SOURCES_BY_FILE[filename]
        extraction = extractor.extract(_fixture_document(source_metadata))
        assert campus in extraction.campus_labels
        version = SourceVersion(
            id=UUID(cast(str, source_metadata["version_id"])),
            source_id=UUID(cast(str, source_metadata["source_id"])),
            content_sha256=cast(str, source_metadata["content_sha256"]),
            content=extraction.text,
            fetched_at=OBSERVED_AT,
            extraction_status=ExtractionStatus.COMPLETE,
            parser_version=extraction.parser_version,
        )
        chunks = SemanticChunker(service.count_wordpieces).chunk(
            version=version,
            document=extraction,
            context=_context_for(source_metadata),
        )
        assert chunks.applicability.campus == campus
        assert any(campus in block.text for block in chunks.blocks)

    prerequisite_metadata = SOURCES_BY_FILE["prerequisite-groups.html"]
    prerequisite = extractor.extract(_fixture_document(prerequisite_metadata))
    prerequisite_version = SourceVersion(
        id=UUID(cast(str, prerequisite_metadata["version_id"])),
        source_id=UUID(cast(str, prerequisite_metadata["source_id"])),
        content_sha256=cast(str, prerequisite_metadata["content_sha256"]),
        content=prerequisite.text,
        fetched_at=OBSERVED_AT,
        extraction_status=ExtractionStatus.COMPLETE,
        parser_version=prerequisite.parser_version,
    )
    prerequisite_chunks = SemanticChunker(service.count_wordpieces).chunk(
        version=prerequisite_version,
        document=prerequisite,
        context=_context_for(prerequisite_metadata),
    )
    assert any(
        cast(dict[str, object], block.structured_content).get("type") == "prerequisite_group"
        for block in prerequisite_chunks.blocks
    )
    assert any("ING 25001" in block.text for block in prerequisite_chunks.blocks)

    malformed_metadata = SOURCES_BY_FILE["malformed-table.html"]
    malformed = extractor.extract(_fixture_document(malformed_metadata))
    assert not malformed.complete
    assert {issue.reason for issue in malformed.issues} == {
        ExtractionFailureReason.MALFORMED_TABLE
    }
    malformed_version = SourceVersion(
        id=UUID(cast(str, malformed_metadata["version_id"])),
        source_id=UUID(cast(str, malformed_metadata["source_id"])),
        content_sha256=cast(str, malformed_metadata["content_sha256"]),
        content=malformed.text,
        fetched_at=OBSERVED_AT,
        extraction_status=ExtractionStatus.INCOMPLETE,
        parser_version=malformed.parser_version,
    )
    with pytest.raises(ChunkingInputError):
        SemanticChunker(service.count_wordpieces).chunk(
            version=malformed_version,
            document=malformed,
            context=ChunkingContext(topic_key="synthetic_malformed_table"),
        )


@requires_database
def test_postgres_pipeline_loads_validates_publishes_expires_and_requalifies(
    governance_session: Session,
) -> None:
    documents = _discover_linked_documents()
    embedding_service = _embedding_service()
    prepared: list[tuple[Source, SourceVersion, tuple[EvidenceBlock, ...], tuple[Embedding, ...]]] = []

    with governance_session.begin():
        for document in documents:
            prepared.append(
                _prepare_and_qualify(
                    governance_session,
                    document,
                    observed_at=OBSERVED_AT,
                    embedding_service=embedding_service,
                )
            )

    source_ids = tuple(item[0].id for item in prepared)
    version_ids = tuple(item[1].id for item in prepared)
    expected_blocks = sum(len(item[2]) for item in prepared)
    assert governance_session.scalar(
        select(func.count()).select_from(Source).where(Source.id.in_(source_ids))
    ) == len(documents)
    assert governance_session.scalar(
        select(func.count()).select_from(SourceVersion).where(SourceVersion.id.in_(version_ids))
    ) == len(documents)
    assert governance_session.scalar(
        select(func.count()).select_from(EvidenceBlock).where(
            EvidenceBlock.version_id.in_(version_ids)
        )
    ) == expected_blocks
    assert governance_session.scalar(
        select(func.count())
        .select_from(Embedding)
        .join(EvidenceBlock, EvidenceBlock.id == Embedding.block_id)
        .where(EvidenceBlock.version_id.in_(version_ids))
    ) == expected_blocks

    governance_session.expire_all()
    published_sources = tuple(
        governance_session.scalars(select(Source).where(Source.id.in_(source_ids))).all()
    )
    assert len(published_sources) == len(documents)
    assert all(source.status is SourceStatus.ELIGIBLE for source in published_sources)
    persisted_embeddings = tuple(
        governance_session.scalars(
            select(Embedding)
            .join(EvidenceBlock, EvidenceBlock.id == Embedding.block_id)
            .where(EvidenceBlock.version_id.in_(version_ids))
        ).all()
    )
    assert all(row.dimensions == EMBEDDING_DIMENSIONS for row in persisted_embeddings)
    assert all(len(row.vector) == EMBEDDING_DIMENSIONS for row in persisted_embeddings)
    assert all(np.isclose(np.linalg.norm(row.vector), 1.0, atol=1e-5) for row in persisted_embeddings)
    governance_session.commit()

    with governance_session.begin():
        refresh = SourceRefreshService(governance_session, clock=lambda: REFRESHED_AT)
        due_ids = {source.id for source in refresh.due_sources()}
        assert set(source_ids) <= due_ids
        expired = refresh.expire_due()
        assert set(source_ids) <= {item.source_id for item in expired}
        assert all(
            item.reason_code == RefreshFailureReason.QUALIFICATION_EXPIRED.value
            for item in expired
            if item.source_id in source_ids
        )

    root_document = documents[0]
    root_source = governance_session.get(Source, source_ids[0])
    assert root_source is not None
    assert root_source.status is SourceStatus.STALE
    root_extraction = StructuredExtractor().extract(root_document)
    original_version_id = prepared[0][1].id
    original_embedding_count = len(prepared[0][3])
    governance_session.commit()

    with governance_session.begin():
        refreshed = SourceVersionService(
            governance_session,
            clock=lambda: REFRESHED_AT,
        ).record(
            source=root_source,
            document=root_document,
            extraction=root_extraction,
        )
        assert not refreshed.created
        assert refreshed.version.id == original_version_id
        assert root_source.status is SourceStatus.STALE

        refreshed_chunks = SemanticChunker(embedding_service.count_wordpieces).chunk(
            version=refreshed.version,
            document=root_extraction,
            context=_context_for(SOURCES_BY_URL[root_document.canonical_url]),
        )
        reused_index = EvidenceIndexer(
            governance_session,
            embedding_service=embedding_service,
        ).index(version=refreshed.version, chunks=refreshed_chunks)
        assert not reused_index.created_blocks
        assert not reused_index.created_embeddings
        requalified = SourceQualificationService(
            governance_session,
            clock=lambda: REFRESHED_AT,
        ).qualify(
            source=root_source,
            version=refreshed.version,
            evidence=QualificationEvidence(
                document=root_document,
                observed_at=REFRESHED_AT,
            ),
        )
        assert requalified.eligible

    governance_session.expire(root_source)
    reloaded_root = governance_session.get(Source, root_source.id)
    assert reloaded_root is not None
    assert reloaded_root.status is SourceStatus.ELIGIBLE
    assert governance_session.scalar(
        select(func.count())
        .select_from(Qualification)
        .where(Qualification.version_id == original_version_id)
    ) == 2
    assert governance_session.scalar(
        select(func.count())
        .select_from(Embedding)
        .join(EvidenceBlock, EvidenceBlock.id == Embedding.block_id)
        .where(EvidenceBlock.version_id == original_version_id)
    ) == original_embedding_count


@requires_database
def test_postgres_pipeline_quarantines_reuses_hashes_and_rolls_back_failed_batches(
    governance_session: Session,
) -> None:
    malformed_metadata = SOURCES_BY_FILE["malformed-table.html"]
    malformed_document = _fixture_document(malformed_metadata)
    malformed_extraction = StructuredExtractor().extract(malformed_document)

    with governance_session.begin():
        malformed_source = _source_for(malformed_document, malformed_extraction.title)
        governance_session.add(malformed_source)
        governance_session.flush()
        malformed_write = SourceVersionService(
            governance_session,
            clock=lambda: OBSERVED_AT,
        ).record(
            source=malformed_source,
            document=malformed_document,
            extraction=malformed_extraction,
        )
        assert malformed_write.quarantined
        failure = SourceQualificationService(
            governance_session,
            clock=lambda: OBSERVED_AT,
        ).qualify(
            source=malformed_source,
            version=malformed_write.version,
            evidence=QualificationEvidence(
                document=malformed_document,
                observed_at=OBSERVED_AT,
            ),
        )
        assert not failure.eligible
        assert {
            QualificationFailureReason.EXTRACTION_INCOMPLETE,
            QualificationFailureReason.EVIDENCE_MISSING,
            QualificationFailureReason.APPLICABILITY_MISSING,
        } <= set(failure.reason_codes)

    assert malformed_source.status is SourceStatus.QUARANTINED
    assert governance_session.scalar(
        select(func.count()).select_from(EvidenceBlock).where(
            EvidenceBlock.version_id == malformed_write.version.id
        )
    ) == 0
    assert malformed_write.version.extraction_status is ExtractionStatus.INCOMPLETE
    assert failure.qualification.status is QualificationStatus.FAILED
    governance_session.commit()

    duplicate_metadata = SOURCES_BY_FILE["duplicate-policy.html"]
    first_document = _fixture_document(duplicate_metadata)
    repeated_document = _fixture_document(
        duplicate_metadata,
        fixture_file="duplicate-policy-repeat.html",
    )
    assert content_sha256(first_document) == content_sha256(repeated_document)
    first_extraction = StructuredExtractor().extract(first_document)
    repeated_extraction = StructuredExtractor().extract(repeated_document)
    with governance_session.begin():
        duplicate_source = _source_for(first_document, first_extraction.title)
        governance_session.add(duplicate_source)
        governance_session.flush()
        first_write = SourceVersionService(
            governance_session,
            clock=lambda: OBSERVED_AT,
        ).record(
            source=duplicate_source,
            document=first_document,
            extraction=first_extraction,
        )
        repeated_write = SourceVersionService(
            governance_session,
            clock=lambda: OBSERVED_AT,
        ).record(
            source=duplicate_source,
            document=repeated_document,
            extraction=repeated_extraction,
        )
        assert first_write.created
        assert not repeated_write.created
        assert repeated_write.version.id == first_write.version.id

    assert governance_session.scalar(
        select(func.count()).select_from(SourceVersion).where(
            SourceVersion.source_id == duplicate_source.id
        )
    ) == 1
    governance_session.commit()

    rollback_metadata = SOURCES_BY_FILE["conflict-eight.html"]
    rollback_document = _fixture_document(rollback_metadata)
    rollback_extraction = StructuredExtractor().extract(rollback_document)
    rollback_source_id = UUID(cast(str, rollback_metadata["source_id"]))
    with pytest.raises(IndexingEmbeddingError), governance_session.begin():
        rollback_source = _source_for(rollback_document, rollback_extraction.title)
        governance_session.add(rollback_source)
        governance_session.flush()
        rollback_write = SourceVersionService(
            governance_session,
            clock=lambda: OBSERVED_AT,
        ).record(
            source=rollback_source,
            document=rollback_document,
            extraction=rollback_extraction,
        )
        chunks = SemanticChunker(_embedding_service().count_wordpieces).chunk(
            version=rollback_write.version,
            document=rollback_extraction,
            context=_context_for(rollback_metadata),
        )
        EvidenceIndexer(
            governance_session,
            embedding_service=_embedding_service(invalid_shape=True),
        ).index(version=rollback_write.version, chunks=chunks)

    assert governance_session.get(Source, rollback_source_id) is None
    assert governance_session.scalar(
        select(func.count()).select_from(SourceVersion).where(
            SourceVersion.source_id == rollback_source_id
        )
    ) == 0
